"""Inspect the one-key deployment and execute its fixed commands."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

ACTIONS = {
    "mapping-start": ("mapping", "start_mapping.sh", "启动建图"),
    "mapping-save": ("mapping", "save_mapping.sh", "保存 BIN"),
    "mapping-stop": ("mapping", "stop_mapping.sh", "结束建图"),
    "convert": ("nav", "start_nav.sh", "转换点云"),
    "prepare": ("nav", "start_nav.sh", "生成导航地图包"),
    "load": ("nav", "start_nav.sh", "加载地图并启动定位"),
    "navigation-stop": ("nav", "stop_nav.sh", "停止导航"),
}
MAP_ACTIONS = {"convert", "prepare", "load"}
PHASES = {
    "请求 Odin1 保存地图", "继续检查上一次保存请求", "等待 BIN 传输完成",
    "正在核对 BIN 文件大小是否稳定", "BIN → PLY", "PLY → PCD",
    "PCD 已生成", "正在生成导航地图包", "准备导航地图包", "启动重定位服务",
    "等待驱动节点及本次配置加载日志", "请求结束建图并恢复配置",
    "正在等待网页加载地图包", "正在验证定位与机体位姿",
}
ERRORS = {
    "BIN → PLY": "BIN 导出失败，请负责人核对厂商转换器与地图。",
    "PLY → PCD": "点云转换失败，请核对 PLY 文件及 Open3D。",
    "生成导航地图包": "地图包生成失败，请核对离线构建日志。",
    "尚未确认驱动": "定位尚未完成，请核对所选地图和相机数据。",
    "节点未出现": "Odin1 尚未确认就绪，请检查相机供电与连接。",
    "端口 9090": "WebSocket 端口被占用，请联系负责人。",
    "配置出现会话外": "驱动配置发生变化，恢复已暂停，请联系负责人。",
    "未确认传输完成": "BIN 保存尚未确认，可以再次选择保存继续核对。",
}
EXPECTED_NODES = {"host_sdk_sample", "odin_cyvet_body_tf", "map_package_manager",
                  "jie_path_node", "rosbridge_websocket", "dog_sdk_bridge", "real_controller"}
EXPECTED_TOPICS = {"/odin1/odometry", "/tf", "/octomap", "/planned_path"}


def shell_record(path):
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            parts = shlex.split(value)
            if len(parts) == 1:
                values[key] = parts[0]
    except (OSError, ValueError):
        return {}
    return values


def nonempty(path):
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def advancing(previous, stamp, now):
    old, last = previous
    if stamp <= 0 or (old is not None and stamp < old):
        return stamp, None
    return stamp, now if old is not None and stamp > old else last


class Telemetry:
    """Read-only ROS graph, advancing Odin timestamps, and fresh map-to-body TF."""

    def __init__(self):
        self.sample = {}

    def run(self):
        try:
            self.observe()
        except Exception as error:
            self.sample = {"error": "ROS 状态采集不可用"}
            print(f"ROS telemetry failed: {type(error).__name__}", flush=True)

    def observe(self):
        from nav_msgs.msg import Odometry
        import rclpy
        from rclpy.qos import qos_profile_sensor_data
        from rclpy.signals import SignalHandlerOptions
        from rclpy.time import Time
        from tf2_ros import Buffer, TransformException, TransformListener

        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        node = rclpy.create_node("odin3d_console_monitor")
        buffer = Buffer()
        listener = TransformListener(buffer, node)
        odom = tf = cloud = (None, None)

        def receive(msg):
            nonlocal odom
            stamp = msg.header.stamp
            odom = advancing(odom, stamp.sec * 10**9 + stamp.nanosec, time.monotonic())

        def receive_cloud(msg):
            nonlocal cloud
            stamp = msg.header.stamp
            cloud = advancing(cloud, stamp.sec * 10**9 + stamp.nanosec, time.monotonic())

        node.create_subscription(
            Odometry, "/odin1/odometry", receive, qos_profile_sensor_data)
        from sensor_msgs.msg import PointCloud2
        node.create_subscription(PointCloud2, "/odin1/cloud_slam", receive_cloud, qos_profile_sensor_data)
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.25)
            now = time.monotonic()
            if now - self.sample.get("sampled", 0) < 0.5:
                continue
            try:
                stamp = buffer.lookup_transform("map", "robot_base_link", Time()).header.stamp
                tf = advancing(tf, stamp.sec * 10**9 + stamp.nanosec, now)
            except TransformException:
                tf = (None, None)
            self.sample = {
                "sampled": now,
                "nodes": sorted(EXPECTED_NODES.intersection(node.get_node_names())),
                "topics": sorted(EXPECTED_TOPICS.intersection(
                    name for name, _ in node.get_topic_names_and_types())),
                "odin": odom[1] is not None and now - odom[1] < 2 and
                any(info.node_name == "host_sdk_sample"
                    for info in node.get_publishers_info_by_topic("/odin1/odometry")),
                "tf": tf[1] is not None and now - tf[1] < 2,
                "cloud": cloud[1] is not None and now - cloud[1] < 2,
            }


class Deployment:
    def __init__(self, env):
        self.env = dict(env)
        self.root = Path(env["ODIN_ROOT"]).resolve()
        self.work = self.root.parent
        self.map_root = Path(env["ODIN_MAP_ROOT"]).resolve()
        self.cache = Path(env["ODIN_PACKAGE_CACHE"])
        self.mapping_record = self.root / "onekey_mapping_state/active.env"
        self.nav_record = self.root / "onekey_nav_state/active/session.env"
        self.telemetry = Telemetry()
        self.motion_gate = None
        self.hashes = {}
        self.job_path = self.work / "web-job.json"
        self.job = self.restore_job()
        self.task = None
        self.lock = asyncio.Lock()

    def restore_job(self):
        try:
            job = json.loads(self.job_path.read_text(encoding="utf-8"))
            if job["status"] == "running":
                job.update(status="failed", message="控制台曾中断，任务结果未知。请核对当前状态。")
                self.job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            return job
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def store_job(self):
        temporary = self.job_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.job, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.job_path)

    def session_owner(self, name):
        try:
            result = subprocess.run(
                ["tmux", "show-environment", "-t", f"={name}", "ODIN_RUN_OWNER"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            return result.stdout.strip().removeprefix("ODIN_RUN_OWNER=") if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def snapshot(self):
        mapping, nav = shell_record(self.mapping_record), shell_record(self.nav_record)
        mapping_active = self.mapping_record.exists()
        nav_active = self.nav_record.parent.is_symlink() or self.nav_record.exists()
        mapping_live = bool(mapping.get("BACKUP_DIR") and
                            self.session_owner("odin_mapping") == mapping["BACKUP_DIR"])
        nav_live = bool(nav.get("RUN_DIR") and self.session_owner("odin_real_nav") == nav["RUN_DIR"])
        dest = mapping.get("DEST")
        saved = False
        if mapping.get("BACKUP_DIR") and dest:
            try:
                receipt = Path(mapping["BACKUP_DIR"]) / "last_saved_map.txt"
                bin_path = Path(receipt.read_text(encoding="utf-8").strip())
                saved = (bin_path.parent == Path(dest) and not bin_path.is_symlink()
                         and nonempty(bin_path))
            except OSError:
                pass
        sample = self.telemetry.sample
        fresh = time.monotonic() - sample.get("sampled", float("-inf")) < 3
        nodes = sample.get("nodes", []) if fresh else []
        topics = sample.get("topics", []) if fresh else []
        odin = sample["odin"] if fresh else None
        bridge = "已发现节点，授权状态未知" if "dog_sdk_bridge" in nodes else ("未连接" if fresh else "未知")
        # The controller may collect a plan before SDK zero-speed takeover.
        external_control = "dog_sdk_bridge" in nodes
        web_prepared = bool(self.motion_gate and self.motion_gate.phase == "prepared")
        localized = bool(nav_live and odin and sample.get("tf") and
                         {"host_sdk_sample", "odin_cyvet_body_tf", "map_package_manager",
                          "jie_path_node"}.issubset(nodes))
        busy = self.task is not None and not self.task.done()
        ready = (self.work / "install.complete").is_file()
        confirmed = self.env.get("ODIN_EXTRINSICS_CONFIRMED") == "yes"
        external_navigation = bool({"host_sdk_sample", "rosbridge_websocket", "jie_path_node",
                                    "real_controller", "dog_sdk_bridge"}.intersection(nodes))
        idle = not mapping_active and not nav_active
        camera_allowed = self.env.get("ODIN_WEB_CAMERA_CONFIRMED") == "yes"
        asset_ready = all(nonempty(path) for path in (
            Path(self.env["ODIN_DRIVER_YAML"]), Path(self.env["DOG_CONTROL_SOURCE"]),
            Path(self.env["DOG_SDK_LIB_DIR"]) / "librobotMotionSdk.so"))
        authorized = (self.env.get("ODIN_VENDOR_AUTHORIZED") == "yes"
                      and self.env.get("DOG_VENDOR_AUTHORIZED") == "yes" and asset_ready)
        ordinary = ready and authorized and not busy
        conditions = {
            "mapping-start": ordinary and idle and camera_allowed and not external_navigation,
            "mapping-save": ordinary and mapping_live and not saved,
            "mapping-stop": not busy and mapping_active and saved,
            "convert": ordinary and idle,
            "prepare": ordinary and idle,
            "load": ordinary and idle and camera_allowed and confirmed and not external_navigation,
            "navigation-stop": not busy and nav_active,
        }
        allowed = [name for name, ok in conditions.items() if ok and
                   self.script(name).is_file()]
        if busy and self.job["action"] in MAP_ACTIONS and self.job["message"] != "启动重定位服务":
            mode = "地图处理中"
        elif busy and self.job["action"] == "load" and not nav_active:
            mode = "启动定位中"
        elif mapping_active:
            mode = "建图中" if mapping_live else "建图待恢复"
        elif nav_active:
            mode = "导航服务运行中" if nav_live else "导航待恢复"
        elif self.job and self.job["status"] == "failed":
            mode = "故障"
        else:
            mode = "空闲"
        package_loaded = False
        if nav_live and all(nav.get(key) for key in ("MAP_PACKAGE", "MAP_ID", "LOG_DIR")):
            marker = f'autoloaded map package: {nav["MAP_PACKAGE"]} map_id={nav["MAP_ID"]}'
            try:
                with (Path(nav["LOG_DIR"]) / "web.log").open("rb") as log:
                    log.seek(max(0, os.fstat(log.fileno()).st_size - 256 * 1024))
                    package_loaded = marker.encode() in log.read()
            except OSError:
                pass
        localized = localized and package_loaded
        if nav_active and localized:
            mode = "导航就绪"
        current_map = Path(nav["MAP_BIN"]).name if package_loaded and nav.get("MAP_BIN") else None
        selected_map = Path(nav["MAP_BIN"]).name if nav.get("MAP_BIN") else None
        current_package = Path(nav["MAP_PACKAGE"]).name if package_loaded else None
        browser_ready = package_loaded and {"rosbridge_websocket", "map_package_manager",
                                            "jie_path_node"}.issubset(nodes)
        return {
            "mode": mode, "agx": "在线", "odin": odin, "bridge": bridge,
            "localization": "持续更新" if localized else "未确认" if nav_active else "未启动",
            "currentMap": current_map, "selectedMap": selected_map, "package": current_package,
            "mapId": self.map_id(Path(nav["MAP_BIN"])) if nav.get("MAP_BIN") else None,
            "navigationRun": Path(nav["RUN_DIR"]).name if nav.get("RUN_DIR") else None,
            "mapping": {"live": mapping_live, "output": dest, "saved": saved,
                        "cloud": sample.get("cloud") if fresh else None,
                        "preview": mapping_live and "rosbridge_websocket" in nodes},
            "navigation": {"live": nav_live, "planner": browser_ready,
                           "canPlan": browser_ready and localized and fresh and
                           (not external_control or web_prepared) and not busy,
                           "execution": "未知（现场控制器在线）" if external_control else
                           ("未启动" if fresh else "未知"), "motion": "未知",
                           "authorization": "Web 运动入口锁定"},
            "job": self.job, "allowed": allowed, "nodes": nodes, "topics": topics,
            "rosKnown": fresh, "cameraConfirmed": camera_allowed, "authorized": authorized,
            "extrinsicsConfirmed": confirmed, "installed": ready,
        }

    def script(self, action):
        component, script, _ = ACTIONS[action]
        return self.root / f"odin_onekey_{component}" / script

    def map_id(self, path):
        try:
            relative = path.relative_to(self.map_root)
        except ValueError:
            return None
        return hashlib.sha256(os.fsencode(relative.as_posix())).hexdigest()

    def catalog(self):
        # Exclude unconfirmed output from the current scan and all symlink escapes.
        mapping = shell_record(self.mapping_record)
        excluded = Path(mapping["DEST"]) if mapping.get("DEST") else None
        receipt = None
        if mapping.get("BACKUP_DIR"):
            try:
                receipt = Path((Path(mapping["BACKUP_DIR"]) / "last_saved_map.txt").read_text().strip())
            except OSError:
                pass
        maps = {}
        for candidate in self.map_root.rglob("*.bin"):
            try:
                if (candidate.is_symlink() or candidate.resolve() != candidate or
                        any(char in str(candidate) for char in "\r\n") or not nonempty(candidate)
                        or (candidate.parent == excluded and candidate != receipt)):
                    continue
                maps[self.map_id(candidate)] = candidate
            except OSError:
                continue
        return maps

    def map_status(self):
        maps = self.catalog()
        self.hashes = {path: value for path, value in self.hashes.items() if path in maps.values()}
        selected = shell_record(self.nav_record).get("MAP_BIN")
        loaded = self.snapshot()["package"] is not None
        result = []
        for map_id, path in maps.items():
            try:
                stat = path.stat()
                signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                cached = self.hashes.get(path)
                if not cached or cached[0] != signature:
                    with path.open("rb") as source:
                        digest = hashlib.sha256()
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(block)
                    self.hashes[path] = (signature, digest.hexdigest())
                sha = self.hashes[path][1]
                if sha == self.env.get("ODIN_LEGACY_BIN_SHA256"):
                    pcd = nonempty(Path(self.env["ODIN_LEGACY_PCD"]))
                    package = Path(self.env["ODIN_LEGACY_PACKAGE"])
                else:
                    pcd = nonempty(self.cache / sha / "map.pcd") or nonempty(self.cache / "converted" / f"{sha}.pcd")
                    package = self.cache / sha / "package"
                result.append({
                    "id": map_id, "name": path.name, "bin": True,
                    "modified": stat.st_mtime,
                    "created": getattr(stat, "st_birthtime", None),
                    "pcd": pcd, "package": all(nonempty(package / file) for file in
                                               ("meta.yaml", "octomap_msg.npz", "layers.npz")),
                    "current": loaded and str(path) == selected,
                })
            except OSError:
                continue
        return sorted(result, key=lambda item: item["modified"], reverse=True)

    async def submit(self, action, map_id):
        async with self.lock:
            state = await asyncio.to_thread(self.snapshot)
            if action not in state["allowed"]:
                if action == "mapping-stop" and state["mapping"]["live"] and not state["mapping"]["saved"]:
                    return 409, "请先保存并确认本次 BIN。"
                if action in {"mapping-start", "load"} and not state["cameraConfirmed"]:
                    return 409, "请负责人确认相机和现场部署。"
                return 409, "当前模式、运行任务或部署条件不允许该操作。请查看设备状态。"
            arguments = []
            if action in MAP_ACTIONS:
                if not isinstance(map_id, str) or not re.fullmatch("[0-9a-f]{64}", map_id):
                    return 400, "地图标识无效。"
                path = (await asyncio.to_thread(self.catalog)).get(map_id)
                if path is None:
                    return 404, "地图已变化或不可用，请刷新列表。"
                option = {"convert": ["--convert"], "prepare": ["--prepare"], "load": []}
                arguments = [*option[action], str(path)]
            elif map_id is not None:
                return 400, "该操作无需地图标识。"
            expected = {}
            if action in {"mapping-save", "mapping-stop"}:
                run = shell_record(self.mapping_record).get("BACKUP_DIR")
                if not run:
                    return 409, "建图会话记录已变化，请刷新。"
                expected["ODIN_EXPECT_MAPPING_RUN"] = run
            if action == "navigation-stop":
                run = shell_record(self.nav_record).get("RUN_DIR")
                if not run:
                    return 409, "导航恢复记录异常，请联系负责人使用维护入口。"
                expected["ODIN_EXPECT_NAV_RUN"] = run
            label = ACTIONS[action][2]
            self.job = {"action": action, "status": "running", "message": label, "started": time.time()}
            self.store_job()
            self.task = asyncio.create_task(self.run(action, arguments, expected))
            return 202, self.job.copy()

    async def run(self, action, arguments, expected):
        label = ACTIONS[action][2]
        message = f"{label}未确认完成，请负责人核对本地运行日志。"
        success = False
        try:
            logs = self.work / "web-logs"
            logs.mkdir(mode=0o700, exist_ok=True)
            log = logs / f"{int(time.time_ns())}-{action}.log"
            error_hint = None
            with log.open("x", encoding="utf-8") as output:
                process = await asyncio.create_subprocess_exec(
                    "bash", str(self.script(action)), *arguments, cwd=self.root,
                    env={**self.env, **expected},
                    stdin=subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                async for line in process.stdout:
                    decoded = line.decode(errors="replace")
                    output.write(decoded)
                    output.flush()
                    phase = re.sub(r"^\[(导航|建图)\] ", "", decoded.strip())
                    if phase in PHASES:
                        self.job["message"] = phase
                        self.store_job()
                    for marker, hint in ERRORS.items():
                        if marker in phase:
                            error_hint = hint
                    if "未确认停止消息送达" in phase:
                        error_hint = "服务已处理停止请求，ROS 停止消息送达未确认，请现场核对。"
                success = await process.wait() == 0
            if success and action == "navigation-stop" and error_hint:
                success = False
            if success:
                message = ("导航进程已退出，驱动配置已恢复。机器狗是否安全停止仍需现场确认。"
                           if action == "navigation-stop" else f"{label}已完成。")
            elif error_hint:
                message = error_hint
        except (OSError, ValueError) as error:
            print(f"{action}: {type(error).__name__}", flush=True)
        finally:
            self.job.update(status="done" if success else "failed", message=message)
            self.store_job()
