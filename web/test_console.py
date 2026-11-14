"""Device-free integration checks. Run python3 -m unittest discover -s web."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from aiohttp import CookieJar, WSServerHandshakeError, WSMsgType, web
from aiohttp.test_utils import TestClient, TestServer

from runtime import ACTIONS, Deployment, advancing, shell_record
from motion import MotionGate
from server import clean_message, create_app


class ConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.base = base
        root = base / "work/integration"
        root.mkdir(parents=True)
        maps = base / "maps"
        maps.mkdir()
        self.bin = maps / "test.bin"
        self.bin.write_bytes(b"fixture-map")
        private = base / "private"
        private.mkdir()
        for name in ("driver.yaml", "dog.py", "librobotMotionSdk.so"):
            (private / name).write_bytes(b"fixture")
        self.deployment = Deployment({
            "ODIN_ROOT": str(root), "ODIN_MAP_ROOT": str(maps),
            "ODIN_PACKAGE_CACHE": str(base / "cache"), "ODIN_DRIVER_YAML": str(private / "driver.yaml"),
            "DOG_CONTROL_SOURCE": str(private / "dog.py"), "DOG_SDK_LIB_DIR": str(private),
            "ODIN_EXTRINSICS_CONFIRMED": "yes", "ODIN_VENDOR_AUTHORIZED": "yes",
            "DOG_VENDOR_AUTHORIZED": "yes", "ODIN_WEB_CAMERA_CONFIRMED": "yes",
        })
        (root.parent / "install.complete").touch()
        for name in ACTIONS:
            script = self.deployment.script(name)
            script.parent.mkdir(parents=True, exist_ok=True)
            script.touch()
        self.owner = {}
        self.deployment.session_owner = lambda name: self.owner.get(name, "")
        self.gate = asyncio.Event()
        self.commands = []
        self.received = []
        fake = web.Application()

        async def upstream(request):
            connection = web.WebSocketResponse()
            await connection.prepare(request)
            async for message in connection:
                self.received.append(json.loads(message.data))
                await connection.send_json({"op": "publish", "topic": "/planned_path", "msg": {"poses": []}})
            return connection

        fake.router.add_get("/", upstream)
        fake_server = TestServer(fake)
        await fake_server.start_server()
        self.addAsyncCleanup(fake_server.close)
        self.origin = "http://console.test:8080"
        self.headers = {"Host": "console.test:8080", "Origin": self.origin}
        planner = base / "planner"
        planner.mkdir()
        (planner / "index.html").write_text("planner fixture")
        app = create_app(self.deployment, self.origin, planner,
                         str(fake_server.make_url("/")))
        self.client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addCleanup(self.gate.set)

    async def open_console(self):
        response = await self.client.get("/api/status", headers=self.headers)
        self.assertEqual(response.status, 200)
        self.headers["X-CSRF-Token"] = (await response.json())["csrf"]

    async def post(self, action, payload=None, headers=None):
        return await self.client.post("/api/actions/" + action, headers=headers or self.headers,
                                      json=payload if payload is not None else {})

    async def state(self):
        response = await self.client.get("/api/status", headers=self.headers)
        self.assertEqual(response.status, 200)
        return await response.json()

    def active_nav(self):
        record = self.deployment.nav_record
        record.parent.mkdir(parents=True)
        logs = self.base / "logs"
        logs.mkdir()
        (logs / "web.log").write_text("autoloaded map package: /test/package map_id=test\n")
        record.write_text(f"RUN_DIR=/test/run\nMAP_BIN={self.bin.as_posix()}\n"
                          f"MAP_PACKAGE=/test/package\nMAP_ID=test\nLOG_DIR={logs.as_posix()}\n")
        self.owner["odin_real_nav"] = "/test/run"
        self.deployment.telemetry.sample = {
            "sampled": time.monotonic(), "odin": False, "tf": False,
            "nodes": ["rosbridge_websocket", "map_package_manager", "jie_path_node"], "topics": [],
        }

    async def test_session_origin_csrf_and_redaction(self):
        self.assertEqual((await self.post("mapping-start")).status, 403)
        await self.open_console()
        self.assertEqual((await self.client.post("/api/login", headers=self.headers, json={})).status, 404)
        self.assertEqual((await self.client.post("/api/motion/auth", headers=self.headers, json={})).status, 404)
        self.assertEqual((await self.client.get("/api/maps", headers=self.headers)).status, 200)
        csrf = self.headers["X-CSRF-Token"]
        self.assertEqual((await self.post("mapping-start", headers={**self.headers, "Origin": "http://elsewhere"})).status, 403)
        self.assertEqual((await self.post("mapping-start", headers={**self.headers, "X-CSRF-Token": "wrong"})).status, 403)
        self.assertEqual((await self.post("arm")).status, 404)
        self.assertEqual((await self.post("mapping-start", {"command": "not accepted"})).status, 400)
        response = await self.client.get("/api/diagnostics", headers=self.headers)
        report = await response.text()
        self.assertNotIn(csrf, report)
        self.assertNotIn(str(self.base), report)
        self.assertIsNone((await self.state())["odin"])

    async def test_duplicate_refresh_and_mode_conflict(self):
        await self.open_console()
        deployment = self.deployment

        async def run(action, arguments, expected):
            self.commands.append(action)
            await self.gate.wait()
            deployment.mapping_record.parent.mkdir(parents=True)
            deployment.mapping_record.write_text("BACKUP_DIR=/test/scan\n")
            self.owner["odin_mapping"] = "/test/scan"
            deployment.job.update(status="done", message="模拟建图启动完成")
            deployment.store_job()

        deployment.run = run
        responses = await asyncio.gather(self.post("mapping-start"), self.post("mapping-start"))
        self.assertEqual(sorted(response.status for response in responses), [202, 409])
        self.assertEqual((await self.state())["job"]["status"], "running")
        self.gate.set()
        await deployment.task
        self.assertEqual(self.commands, ["mapping-start"])
        self.assertEqual((await self.state())["mode"], "建图中")
        self.assertEqual((await self.post("load", {"mapId": deployment.map_id(self.bin)})).status, 409)
        self.assertEqual((await self.post("mapping-stop")).status, 409)
        restarted = Deployment(deployment.env)
        self.assertEqual(restarted.job["status"], "done")

    async def test_failure_preserves_bin_and_invalid_map_rejected(self):
        await self.open_console()
        self.assertEqual((await self.post("convert", {"mapId": "../test.bin"})).status, 400)
        self.assertEqual((await self.post("prepare", {"mapId": "0" * 64})).status, 404)
        original = self.bin.read_bytes()
        deployment = self.deployment

        class FailedConverter:
            async def wait(self):
                return 2

            @property
            def stdout(self):
                async def lines():
                    yield "[导航] BIN → PLY\n".encode()
                    yield "错误：BIN → PLY 失败\n".encode()
                return lines()

        async def launch(*argv, **kwargs):
            self.assertEqual(argv[-2:], ("--convert", str(self.bin)))
            return FailedConverter()

        with patch("runtime.asyncio.create_subprocess_exec", side_effect=launch):
            self.assertEqual((await self.post("convert", {"mapId": deployment.map_id(self.bin)})).status, 202)
            await deployment.task
        self.assertEqual((await self.state())["job"]["status"], "failed")
        self.assertIn("BIN 导出失败", deployment.job["message"])
        self.assertEqual(self.bin.read_bytes(), original)

    async def test_stop_without_feedback_is_not_success(self):
        await self.open_console()
        self.active_nav()

        class IncompleteStop:
            async def wait(self):
                return 0

            @property
            def stdout(self):
                async def lines():
                    yield "未确认停止消息送达\n".encode()
                return lines()

        async def launch(*argv, **kwargs):
            self.assertTrue(argv[1].endswith("stop_nav.sh"))
            self.assertEqual(kwargs["env"]["ODIN_EXPECT_NAV_RUN"], "/test/run")
            return IncompleteStop()

        with patch("runtime.asyncio.create_subprocess_exec", side_effect=launch):
            self.assertEqual((await self.post("navigation-stop")).status, 202)
            await self.deployment.task
        self.assertEqual((await self.state())["job"]["status"], "failed")

    async def test_planner_compatibility_proxy_and_reconnect(self):
        await self.open_console()
        response = await self.client.get("/index.html", headers=self.headers, allow_redirects=False)
        self.assertEqual(response.headers["Location"], "/#navigation")
        self.assertEqual((await self.client.get("/planner/", headers=self.headers)).status, 200)
        self.active_nav()
        with self.assertRaises(WSServerHandshakeError):
            await self.client.ws_connect("/planner/socket", headers={**self.headers, "Origin": "http://other"})
        for _ in range(2):
            async with self.client.ws_connect("/planner/socket", headers=self.headers) as socket:
                await socket.send_json({"op": "subscribe", "topic": "/planned_path", "type": "ignored"})
                response = await socket.receive_json(timeout=3)
                self.assertEqual(response["topic"], "/planned_path")
                await socket.send_json({"op": "publish", "topic": "/start_navigation", "msg": {"data": True}})
                self.assertEqual((await socket.receive(timeout=3)).type, WSMsgType.CLOSE)
                self.assertEqual(socket.close_code, 1008)
        self.assertEqual([item["topic"] for item in self.received], ["/planned_path"] * 2)
        self.assertEqual((await self.state())["currentMap"], "test.bin")

    async def test_motion_confirmation_and_simulated_lifecycle(self):
        await self.open_console()
        url = "/api/motion/"
        post = lambda action, body: self.client.post(url + action, headers=self.headers, json=body)
        self.assertEqual((await post("auth", {"token": "unused"})).status, 404)
        self.assertEqual((await post("prepare", {})).status, 400)
        self.assertEqual((await post("start", {})).status, 409)
        self.assertEqual((await post("prepare", {"confirmed": True})).status, 409)

        self.active_nav()
        self.deployment.env["DOG_DEVICE_ID"] = "fixture-only"
        sample = self.deployment.telemetry.sample
        sample.update(odin=True, tf=True, nodes=["host_sdk_sample", "odin_cyvet_body_tf",
                                             "map_package_manager", "jie_path_node",
                                             "rosbridge_websocket", "real_controller"])
        gate = self.deployment.motion_gate
        gate._spawn_watchdog = lambda: None
        release_prepare = asyncio.Event()
        async def simulated(*args, **kwargs):
            if "--web-approved" in args:
                await release_prepare.wait()
            return True, "模拟反馈"
        gate._command = simulated
        self.assertTrue((await self.state())["navigation"]["canPlan"])
        try:
            self.assertEqual((await post("prepare", {"confirmed": True})).status, 202)
            self.assertEqual((await post("prepare", {"confirmed": True})).status, 409)
            sample["nodes"].append("dog_sdk_bridge")
            release_prepare.set()
            await gate.task
            self.assertEqual((await self.state())["motionGate"]["phase"], "prepared")
            self.assertFalse((await self.state())["motionGate"]["canStart"])
            record = shell_record(self.deployment.nav_record)
            (Path(record["LOG_DIR"]) / "controller.log").write_text(
                "Received planned_path with 5 poses. Waiting for /start_navigation confirmation.\n")
            (Path(record["LOG_DIR"]) / "sdk.log").write_text("enabled=False SDK参数={}\n")
            self.assertTrue((await self.state())["motionGate"]["canStart"])
            self.assertEqual((await post("start", {})).status, 202)
            await gate.task
            self.assertEqual((await self.state())["motionGate"]["phase"], "moving")
            self.assertEqual((await post("stop", {})).status, 202)
            await gate.task
            self.assertEqual((await self.state())["motionGate"]["phase"], "locked")
        finally:
            gate.phase = "locked"
            gate.lease.unlink(missing_ok=True)

    async def test_motion_stop_without_sdk_feedback_remains_uncertain(self):
        await self.open_console()
        self.active_nav()
        gate = self.deployment.motion_gate
        gate.phase, gate.run = "moving", "/test/run"
        gate.session = "fixture-session"
        async def no_ack(*args, **kwargs):
            return False, "SDK 未确认禁用"
        gate._command = no_ack
        response = await self.client.post("/api/motion/stop", headers=self.headers, json={})
        self.assertEqual(response.status, 202)
        await gate.task
        status = (await self.state())["motionGate"]
        self.assertEqual(status["phase"], "uncertain")
        self.assertIn("物理急停", status["message"])

    async def test_motion_lease_expiry_locks_without_auto_resume(self):
        await self.open_console()
        self.active_nav()
        gate = self.deployment.motion_gate
        gate.phase, gate.run = "moving", "/test/run"
        gate.deadline = time.monotonic() - 1
        async def simulated_stop(*args, **kwargs):
            return True, "模拟 SDK 禁用反馈"
        gate._command = simulated_stop
        await asyncio.sleep(0.7)
        self.assertEqual((await self.state())["motionGate"]["phase"], "locked")
        self.assertEqual((await self.client.post("/api/motion/start", headers=self.headers, json={})).status, 409)

    async def test_stop_preempts_pending_start(self):
        await self.open_console()
        self.active_nav()
        gate = self.deployment.motion_gate
        gate.phase, gate.run = "starting", "/test/run"
        pending = asyncio.Event()
        async def waiting_start():
            await pending.wait()
        old_task = asyncio.create_task(waiting_start())
        gate.task = old_task
        called = []
        async def simulated_stop(*args, **kwargs):
            called.append(args)
            return True, "SDK 禁用已确认"
        gate._command = simulated_stop
        response = await self.client.post("/api/motion/stop", headers=self.headers, json={})
        self.assertEqual(response.status, 202)
        await gate.task
        self.assertTrue(old_task.cancelled())
        self.assertEqual(called[0][-1], "stop")
        self.assertEqual(gate.phase, "locked")

    async def test_restart_recovers_lease_by_stopping(self):
        lease = self.deployment.work / "motion-lease.json"
        lease.write_text(json.dumps({"run": "/test/run", "expires": time.monotonic() + 2}))
        recovered = MotionGate(self.deployment)
        self.assertEqual(recovered.phase, "stopping")
        calls = []
        async def simulated_stop(*args, **kwargs):
            calls.append(args[-1])
            return True, "SDK 禁用已确认"
        recovered._command = simulated_stop
        recovered.recover()
        await recovered.task
        self.assertEqual(calls, ["stop"])
        self.assertEqual(recovered.phase, "locked")
        self.assertFalse(lease.exists())

    async def test_monitor_does_not_repeat_manual_stop_after_snapshot(self):
        self.active_nav()
        gate = MotionGate(self.deployment)
        gate.phase, gate.run = "moving", "/test/run"
        gate.deadline = time.monotonic() - 1
        entered, release = threading.Event(), threading.Event()
        original_snapshot = self.deployment.snapshot
        def delayed_snapshot():
            entered.set()
            release.wait(3)
            return original_snapshot()
        self.deployment.snapshot = delayed_snapshot
        calls = []
        async def simulated_stop(*args, **kwargs):
            calls.append(args[-1])
            return True, "SDK 禁用已确认"
        gate._command = simulated_stop
        monitor = asyncio.create_task(gate.monitor())
        try:
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(entered.wait), 2))
            code, _ = await gate.stop()
            self.assertEqual(code, 202)
            await gate.task
            release.set()
            await asyncio.sleep(0.2)
            self.assertEqual(calls, ["stop"])
            self.assertEqual(gate.phase, "locked")
        finally:
            release.set()
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)


class ValidationTests(unittest.TestCase):
    def test_independent_watchdog_uses_fixed_stop_on_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lease, script, marker = (root / name for name in ("lease.json", "fake.sh", "called.txt"))
            lease.write_text(json.dumps({"run": "/fixture/run", "expires": 0}))
            script.write_text('printf "%s|%s" "$1" "$ODIN_EXPECT_NAV_RUN" > "$MOTION_TEST_MARKER"\n')
            env = {**os.environ, "MOTION_TEST_MARKER": str(marker)}
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("motion_watchdog.py")),
                                     str(lease), str(script), "/fixture/run"],
                                    env=env, timeout=5, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(marker.read_text(), "stop|/fixture/run")
            self.assertFalse(lease.exists())

    def test_fixed_planning_message_and_unknown_time(self):
        point = {"header": {"frame_id": "map"}, "point": {"x": 1, "y": 2, "z": 3}}
        self.assertEqual(clean_message({"op": "publish", "topic": "/goal_point", "msg": point})["msg"], point)
        for topic in ("/dog_bridge/enable", "/cmd_vel", "/start_navigation"):
            with self.assertRaises(ValueError):
                clean_message({"op": "publish", "topic": topic, "msg": {}})
        self.assertEqual(advancing((None, None), 42, 1), (42, None))
        self.assertEqual(advancing((42, None), 43, 2), (43, 2))
        self.assertEqual(advancing((43, 2), 40, 3), (40, None))
