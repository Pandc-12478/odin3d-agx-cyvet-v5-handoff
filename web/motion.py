"""Server-owned motion gate around the existing controller and SDK bridge."""
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from runtime import shell_record


def log_tail(path, limit=65536):
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - limit))
        return stream.read().decode("utf-8", "replace").splitlines()


class MotionGate:
    def __init__(self, deployment):
        self.deployment = deployment
        self.phase = "locked"
        self.message = "运动锁定；现场负责人需确认后准备。"
        self.run = None
        self.session = None
        self.deadline = 0.0
        self.task = None
        self.lock = asyncio.Lock()
        self.lease = deployment.work / "motion-lease.json"
        self.script = Path(__file__).with_name("motion_nav.sh")
        self.watchdog = Path(__file__).with_name("motion_watchdog.py")
        if self.lease.exists():
            try:
                prior = json.loads(self.lease.read_text(encoding="utf-8"))
                if isinstance(prior.get("run"), str) and prior["run"]:
                    self.run = prior["run"]
                    self.phase = "stopping"
                    self.message = "发现上次运动租约，正在恢复受控停止；请现场准备物理急停。"
                else:
                    raise ValueError("invalid motion lease")
            except (OSError, ValueError, TypeError):
                self.phase = "uncertain"
                self.message = "运动租约损坏；请立即现场核对并使用物理急停。"

    def recover(self):
        if self.phase == "stopping" and self.run:
            self.task = asyncio.create_task(self._stop())

    def report(self, session_key=None):
        state = self.deployment.snapshot()
        active = self.phase in {"preparing", "prepared", "starting", "moving", "stopping"}
        changed = active and (self.run != shell_record(self.deployment.nav_record).get("RUN_DIR") or
                              not state["navigation"]["live"])
        return {
            "phase": self.phase, "message": "控制链已变化，正在尝试受控停止；请现场准备物理急停。" if changed else self.message,
            "sdk": state["bridge"], "localization": state["localization"],
            "mode": state["mode"], "run": state["navigationRun"],
            "owns": bool(active and self.session == session_key),
            "canPrepare": self.phase == "locked" and self._ready(state) and
                bool(self.deployment.env.get("DOG_DEVICE_ID")) and
                "dog_sdk_bridge" not in state["nodes"],
            "canStart": self.phase == "prepared" and self._ready(state) and
                self._control_ready(state) and self._pending_plan(),
            "canStop": active or self.phase == "uncertain",
        }

    def _ready(self, state):
        return bool(state["navigation"]["live"] and state["navigation"]["planner"] and
                    state["localization"] == "持续更新" and state["authorized"] and
                    state["extrinsicsConfirmed"] and not state["mapping"]["live"] and
                    not (self.deployment.task and not self.deployment.task.done()))

    def _control_ready(self, state):
        return {"real_controller", "dog_sdk_bridge"}.issubset(state["nodes"])

    def _pending_plan(self):
        record = shell_record(self.deployment.nav_record)
        if record.get("RUN_DIR") != self.run:
            return False
        try:
            lines = log_tail(Path(record["LOG_DIR"]) / "controller.log")
        except (OSError, KeyError):
            return False
        pending = False
        for line in lines:
            if re.search(r"Received planned_path with [1-9][0-9]* poses.*Waiting", line):
                pending = True
            elif any(marker in line for marker in (
                    "Received empty planned_path", "Navigation execution started",
                    "Stop navigation requested", "Navigation start denied")):
                pending = False
        return pending

    def _sdk_enabled(self):
        record = shell_record(self.deployment.nav_record)
        if record.get("RUN_DIR") != self.run:
            return False
        try:
            lines = log_tail(Path(record["LOG_DIR"]) / "sdk.log", 16384)
        except (OSError, KeyError):
            return False
        reports = [line for line in lines if "SDK参数=" in line and "enabled=" in line]
        return bool(reports and "enabled=True" in reports[-1])

    def _write_lease(self):
        data = {"run": self.run, "expires": time.monotonic() + 5.0}
        temporary = self.lease.with_suffix(".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.lease)
        self.deadline = data["expires"]

    def heartbeat(self, session_key):
        if self.session != session_key or self.phase not in {"preparing", "prepared", "starting", "moving"}:
            return False
        self._write_lease()
        return True

    async def _command(self, *args, env=None, timeout=50):
        process = await asyncio.create_subprocess_exec(
            "bash", *map(str, args), env=env or self.deployment.env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), 2)
            except asyncio.TimeoutError:
                # Only this backend's own fixed-operation process group.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            if isinstance(error, asyncio.CancelledError):
                raise
            return False, "受控操作超时。"
        lines = output.decode("utf-8", "replace").strip().splitlines()
        return process.returncode == 0, (lines[-1][:240] if lines else "无执行反馈")

    def _spawn_watchdog(self):
        log = (self.deployment.work / "motion-watchdog.log").open("ab", buffering=0)
        try:
            subprocess.Popen([sys.executable, str(self.watchdog), str(self.lease),
                              str(self.script), self.run], env=self.deployment.env,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True, close_fds=True)
        finally:
            log.close()

    async def prepare(self, session_key):
        async with self.lock:
            state = await asyncio.to_thread(self.deployment.snapshot)
            if self.phase != "locked" or not self._ready(state):
                return 409, "地图、定位或运行模式尚未满足准备条件。"
            if not self.deployment.env.get("DOG_DEVICE_ID"):
                return 409, "负责人尚未在 AGX 私有配置中填写设备 ID。"
            if "dog_sdk_bridge" in state["nodes"]:
                return 409, "已有 SDK Bridge，请先核对来源。"
            self.run = shell_record(self.deployment.nav_record).get("RUN_DIR")
            if not self.run:
                return 409, "导航会话记录缺失。"
            self.session = session_key
            self.phase, self.message = "preparing", "现场确认已收到，正在执行零速接管。"
            self._write_lease()
            self._spawn_watchdog()
            self.task = asyncio.create_task(self._prepare())
            return 202, "零速接管请求已受理，等待 SDK 真实反馈。"

    async def _prepare(self):
        env = {**self.deployment.env, "ODIN_WEB_APPROVAL_RUN": self.run}
        ok, detail = await self._command(self.deployment.root / "odin_onekey_nav/arm_nav.sh",
                                         "--web-approved", env=env, timeout=50)
        state = await asyncio.to_thread(self.deployment.snapshot)
        if ok and self.phase == "preparing" and self._ready(state) and self._control_ready(state):
            self.phase = "prepared"
            self.message = ("SDK 已报告零速禁用，控制器已有待启动路径；请核对后点击开始导航。"
                            if self._pending_plan() else "SDK 已报告零速禁用；请先在三维地图选点规划。")
            return
        if self.phase == "preparing":
            self.phase, self.message = "stopping", f"零速接管未确认：{detail}；正在执行受控停止。"
            await self._stop()

    async def start(self, session_key):
        async with self.lock:
            state = await asyncio.to_thread(self.deployment.snapshot)
            if (self.phase != "prepared" or self.session != session_key or not self._ready(state) or
                    not self._control_ready(state) or not self._pending_plan()):
                return 409, "尚未完成零速接管，或定位、控制链、待启动路径已变化。"
            if time.monotonic() > self.deadline:
                return 409, "网页心跳超时，运动保持锁定。"
            self.phase, self.message = "starting", "开始请求已受理，等待控制器和 SDK 确认。"
            self.task = asyncio.create_task(self._start())
            return 202, "开始请求已受理，等待真实反馈。"

    async def _start(self):
        env = {**self.deployment.env, "ODIN_EXPECT_NAV_RUN": self.run}
        ok, detail = await self._command(self.script, "start", env=env, timeout=30)
        if ok and self.phase == "starting":
            self.phase, self.message = "moving", "控制器和 SDK 已确认导航使能；机器狗运动以现场观察为准。"
        elif self.phase == "starting":
            self.phase, self.message = "stopping", f"运动使能未确认：{detail}；正在执行受控停止。"
            await self._stop()

    async def stop(self):
        async with self.lock:
            if self.phase in {"locked", "stopping"}:
                return 409, "当前没有可停止的 Web 运动会话。"
            previous = self.task if self.task and not self.task.done() else None
            self.phase, self.message = "stopping", "停止请求已受理；正在等待 SDK 禁用反馈。"
            if previous:
                previous.cancel()
            self.task = asyncio.create_task(self._stop_after(previous))
            return 202, "停止请求已受理，尚未确认设备停止；现场保留物理急停。"

    async def _stop_after(self, previous):
        if previous:
            await asyncio.gather(previous, return_exceptions=True)
        await self._stop()

    async def _stop(self):
        if self.lease.exists():
            temporary = self.lease.with_suffix(".tmp")
            temporary.write_text(json.dumps({"run": self.run, "expires": time.monotonic() + 70}),
                                 encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(self.lease)
        env = {**self.deployment.env, "ODIN_EXPECT_NAV_RUN": self.run}
        ok, detail = await self._command(self.script, "stop", env=env, timeout=60)
        self.lease.unlink(missing_ok=True)
        if ok:
            self.phase, self.message = "locked", "SDK 已确认禁用并退出；请现场确认机器狗状态。重新运动须重载地图并再次准备。"
        else:
            self.phase, self.message = "uncertain", f"停止未获完整确认：{detail}。请立即使用物理急停并联系负责人。"

    async def monitor(self):
        while True:
            await asyncio.sleep(0.5)
            if self.phase not in {"preparing", "prepared", "starting", "moving"}:
                continue
            state = await asyncio.to_thread(self.deployment.snapshot)
            async with self.lock:
                # A manual stop may have begun while the read-only snapshot ran.
                # Never cancel an in-flight stop or start a second stop for that run.
                if self.phase not in {"preparing", "prepared", "starting", "moving"}:
                    continue
                if (time.monotonic() > self.deadline or
                        shell_record(self.deployment.nav_record).get("RUN_DIR") != self.run or
                        not self._ready(state) or
                        (self.phase in {"prepared", "moving"} and not self._control_ready(state)) or
                        (self.phase == "moving" and not self._sdk_enabled())):
                    previous = self.task if self.task and not self.task.done() else None
                    self.phase, self.message = "stopping", "心跳、定位或控制链异常；正在锁定并停止。"
                    if previous:
                        previous.cancel()
                    self.task = asyncio.create_task(self._stop_after(previous))

