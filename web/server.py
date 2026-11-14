"""Odin3D single-origin console and restricted bridge for the legacy planner."""
import argparse
import asyncio
from collections import OrderedDict
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import ssl
import threading
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web

from runtime import ACTIONS, Deployment
from motion import MotionGate

STATIC = Path(__file__).resolve().parent / "static"
PLANNER_FILES = {"index.html", "main.js", "styles.css", "vendor/roslib.min.js",
                 "vendor/three.module.js", "vendor/jsm/controls/OrbitControls.js"}
READ_TOPICS = {
    "/octomap_occupied_markers": "visualization_msgs/Marker",
    "/preblocked_cells_markers": "visualization_msgs/Marker",
    "/traversable_cells_markers": "visualization_msgs/Marker",
    "/risk_cost_cells": "sensor_msgs/PointCloud2",
    "/selection_markers": "visualization_msgs/MarkerArray",
    "/planned_path": "nav_msgs/Path",
    "/tracking_point_marker": "visualization_msgs/Marker",
    "/web_selection_status": "std_msgs/String",
    "/tf": "tf2_msgs/TFMessage",
    "/odin_cyvet/body_tf_static": "tf2_msgs/TFMessage",
    "/odin1/cloud_slam": "sensor_msgs/PointCloud2",
}
WRITE_TOPICS = {"/start_point": "geometry_msgs/PointStamped",
                "/goal_point": "geometry_msgs/PointStamped",
                "/goal_pose": "geometry_msgs/PoseStamped"}
COOKIE = "odin3d_session"
SESSION_SECONDS = 8 * 3600


def clean_message(payload):
    """Reconstruct permitted ROSBridge messages instead of forwarding client JSON."""
    if not isinstance(payload, dict):
        raise ValueError
    operation, topic = payload.get("op"), payload.get("topic")
    if not isinstance(topic, str) or not isinstance(operation, str):
        raise ValueError
    if operation in ("subscribe", "unsubscribe") and topic in READ_TOPICS:
        result = {"op": operation, "topic": topic}
        if operation == "subscribe":
            result.update(type=READ_TOPICS[topic], queue_length=1)
            if topic == "/odin1/cloud_slam":
                result["throttle_rate"] = 250
        return result
    if operation in ("advertise", "unadvertise") and topic in WRITE_TOPICS:
        result = {"op": operation, "topic": topic}
        if operation == "advertise":
            result["type"] = WRITE_TOPICS[topic]
        return result
    if operation != "publish" or topic not in WRITE_TOPICS:
        raise ValueError
    msg = payload["msg"]
    if msg["header"]["frame_id"] != "map":
        raise ValueError

    def vector(value, axes, limit):
        result = {key: value[key] for key in axes}
        if any(type(number) not in (float, int) or not math.isfinite(number) or abs(number) > limit
               for number in result.values()):
            raise ValueError
        return result

    data = {"header": {"frame_id": "map"}}
    if topic == "/goal_pose":
        pose = msg["pose"]
        data["pose"] = {"position": vector(pose["position"], "xyz", 10000),
                        "orientation": vector(pose["orientation"], "xyzw", 1)}
    else:
        data["point"] = vector(msg["point"], "xyz", 10000)
    return {"op": "publish", "topic": topic, "msg": data}


def create_app(deployment, origin, planner_root, bridge_url="ws://127.0.0.1:9090"):
    sessions = OrderedDict()
    active_sockets = set()
    motion_gate = MotionGate(deployment)
    deployment.motion_gate = motion_gate
    maps_lock = asyncio.Lock()
    secure = origin.startswith("https:")
    host = origin.split("://", 1)[1]

    def session(request):
        key = request.cookies.get(COOKIE)
        entry = sessions.get(key)
        if entry and entry[1] > time.monotonic():
            return entry
        sessions.pop(key, None)
        return None

    def reject(code, message):
        return web.json_response({"error": message}, status=code)

    @web.middleware
    async def security(request, handler):
        if request.headers.get("Host") != host:
            return reject(403, "请用负责人提供的控制台地址打开。")
        if request.method == "POST" or request.path == "/planner/socket":
            if request.headers.get("Origin") != origin:
                return reject(403, "请求来源无效，请从控制台操作。")
        try:
            response = await handler(request)
        except web.HTTPException:
            raise
        except Exception as error:
            print(f"web request failed: {type(error).__name__}", flush=True)
            response = reject(500, "服务暂时无法完成请求，请刷新或联系负责人。")
        if not response.prepared:
            response.headers.update({
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "SAMEORIGIN",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    f"connect-src 'self' {'wss' if secure else 'ws'}://{host}; "
                    "img-src 'self' data:; frame-src 'self'; frame-ancestors 'self'; "
                    "base-uri 'none'; form-action 'self'; object-src 'none'"
                ),
            })
        return response

    app = web.Application(client_max_size=4096, middlewares=[security])

    def new_session():
        now = time.monotonic()
        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sessions[cookie] = (csrf, now + SESSION_SECONDS)
        if len(sessions) > 128:
            sessions.popitem(last=False)
        return cookie, csrf

    def session_cookie(response, cookie):
        response.set_cookie(COOKIE, cookie, httponly=True, samesite="Strict",
                            secure=secure, max_age=SESSION_SECONDS, path="/")

    async def status(request):
        current = session(request)
        if not current:
            cookie, csrf = new_session()
            current = (csrf, time.monotonic() + SESSION_SECONDS)
        else:
            cookie = None
        state = await asyncio.to_thread(deployment.snapshot)
        state["motionGate"] = await asyncio.to_thread(motion_gate.report, cookie or request.cookies.get(COOKIE))
        state["navigation"].update(motion=state["motionGate"]["message"])
        state["navigation"]["execution"] = {
            "preparing": "零速接管中", "prepared": "零速接管完成，等待开始导航",
            "starting": "开始请求已发送，等待确认", "moving": "控制器和 SDK 已确认执行",
            "stopping": "停止请求已发送，等待确认", "uncertain": "停止状态未确认",
        }.get(state["motionGate"]["phase"], "未启动")
        state.update(csrf=current[0], address=origin)
        response = web.json_response(state)
        if cookie:
            session_cookie(response, cookie)
        return response

    async def maps(request):
        if not session(request):
            return reject(403, "请刷新控制台后重试。")
        async with maps_lock:
            return web.json_response(await asyncio.to_thread(deployment.map_status))

    async def diagnostics(request):
        if not session(request):
            return reject(403, "请刷新控制台后重试。")
        state = await asyncio.to_thread(deployment.snapshot)
        keys = ("mode", "agx", "odin", "bridge", "localization", "navigation",
                "nodes", "topics", "rosKnown", "installed", "authorized", "job")
        report = {key: state[key] for key in keys}
        return web.json_response(report, headers={
            "Content-Disposition": 'attachment; filename="odin3d-diagnostics.json"'})

    async def action(request):
        current = session(request)
        if not current:
            return reject(403, "请刷新控制台后重试。")
        if not secrets.compare_digest(request.headers.get("X-CSRF-Token", "").encode(), current[0].encode()):
            return reject(403, "操作校验已过期，请刷新。")
        name = request.match_info["action"]
        if name not in ACTIONS:
            return reject(404, "不支持的操作。")
        if name == "navigation-stop" and motion_gate and motion_gate.phase in {
                "preparing", "prepared", "starting", "moving", "stopping"}:
            return reject(409, "请先使用“停止运动”，等待 SDK 禁用反馈。")
        try:
            body = await request.json()
        except ValueError:
            return reject(400, "无效的操作请求。")
        if not isinstance(body, dict) or set(body) - {"mapId"}:
            return reject(400, "请求字段无效。")
        code, result = await deployment.submit(name, body.get("mapId"))
        return web.json_response(result if code == 202 else {"error": result}, status=code)

    def motion_session(request):
        current = session(request)
        if not current:
            return 403, "请刷新控制台后重试。"
        if not secrets.compare_digest(request.headers.get("X-CSRF-Token", "").encode(), current[0].encode()):
            return 403, "操作校验已过期，请刷新。"
        return 200, request.cookies.get(COOKIE)

    async def motion_action(request):
        code, key = motion_session(request)
        if code != 200:
            return reject(code, key)
        operation = request.match_info["operation"]
        if operation not in {"prepare", "start", "stop", "heartbeat"}:
            return reject(404, "不支持的运动操作。")
        try:
            body = await request.json()
        except ValueError:
            return reject(400, "请求无效。")
        if not isinstance(body, dict):
            return reject(400, "请求无效。")
        if operation == "heartbeat":
            if body or not motion_gate.heartbeat(key):
                return reject(409, "运动会话已锁定或不属于当前浏览器。")
            return web.json_response({"ok": True})
        if operation == "stop":
            if body:
                return reject(400, "停止请求不接受参数。")
            code, message = await motion_gate.stop()
        else:
            if operation == "prepare":
                if body != {"confirmed": True}:
                    return reject(400, "请先确认现场急停、范围与姿态。")
                code, message = await motion_gate.prepare(key)
            else:
                if body:
                    return reject(400, "开始请求不接受参数。")
                code, message = await motion_gate.start(key)
        return web.json_response({"message": message} if code == 202 else {"error": message}, status=code)

    async def planner(request):
        if not session(request):
            return reject(403, "请刷新控制台后重试。")
        name = request.match_info.get("file", "") or "index.html"
        if name not in PLANNER_FILES or not (planner_root / name).is_file():
            return reject(404, "三维页面资源未安装，请联系负责人。")
        return web.FileResponse(planner_root / name)

    async def bridge(request):
        if not session(request):
            return reject(403, "请刷新控制台后重试。")
        state = await asyncio.to_thread(deployment.snapshot)
        preview = request.query.get("preview") == "odin"
        if not (state["mapping"]["preview"] if preview else state["navigation"]["planner"]) or len(active_sockets) >= 8:
            return reject(409, "规划服务尚未就绪或连接数已满。")
        browser = web.WebSocketResponse(heartbeat=25, max_msg_size=8192)
        active_sockets.add(browser)
        try:
            async with ClientSession(timeout=ClientTimeout(total=4)) as client:
                try:
                    upstream = await client.ws_connect(bridge_url, max_msg_size=32 * 1024 * 1024)
                except (ClientError, OSError, TimeoutError):
                    return reject(503, "ROSBridge 未连接。")
                try:
                    await browser.prepare(request)

                    async def browser_to_ros():
                        subscribed, advertised = set(), set()
                        publish_count, publish_window = 0, time.monotonic()
                        async for frame in browser:
                            if not session(request):
                                return
                            if frame.type != WSMsgType.TEXT:
                                return
                            try:
                                data = clean_message(json.loads(frame.data))
                            except (ValueError, KeyError, TypeError, OverflowError):
                                await browser.close(code=1008, message=b"operation not permitted")
                                return
                            topic, op = data["topic"], data["op"]
                            if preview and (op not in ("subscribe", "unsubscribe") or
                                            topic != "/odin1/cloud_slam"):
                                await browser.close(code=1008, message=b"preview is read only")
                                return
                            if op == "publish":
                                now = time.monotonic()
                                if now - publish_window >= 1:
                                    publish_count, publish_window = 0, now
                                publish_count += 1
                                sample = deployment.telemetry.sample
                                if (publish_count > 24 or
                                        not (await asyncio.to_thread(deployment.snapshot))["navigation"]["canPlan"] or
                                        (deployment.task is not None and not deployment.task.done()) or
                                        now - sample.get("sampled", float("-inf")) >= 3):
                                    await browser.close(code=1008, message=b"planning unavailable")
                                    return
                            if op in ("subscribe", "advertise"):
                                topics = subscribed if op == "subscribe" else advertised
                                if topic in topics:
                                    continue
                                topics.add(topic)
                            if op == "unsubscribe":
                                subscribed.discard(topic)
                            if op == "unadvertise":
                                advertised.discard(topic)
                            await upstream.send_json(data)

                    async def ros_to_browser():
                        async for frame in upstream:
                            if not session(request):
                                return
                            if frame.type == WSMsgType.TEXT:
                                await browser.send_str(frame.data)
                            elif frame.type == WSMsgType.BINARY:
                                await browser.send_bytes(frame.data)

                    tasks = [asyncio.create_task(relay()) for relay in (browser_to_ros, ros_to_browser)]
                    try:
                        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        await browser.close()
                finally:
                    await upstream.close()
        finally:
            active_sockets.discard(browser)
        return browser

    async def health(_):
        return web.Response(text="odin3d-console")

    async def index(_):
        return web.FileResponse(STATIC / "index.html")

    async def asset(request):
        return web.FileResponse(STATIC / request.match_info["file"])

    async def legacy(_):
        raise web.HTTPFound("/#navigation")

    async def shutdown(_):
        await asyncio.gather(*(socket.close(code=1001) for socket in tuple(active_sockets)))
        if deployment.task and not deployment.task.done():
            await asyncio.shield(deployment.task)
        if motion_gate and motion_gate.phase in {"preparing", "prepared", "starting", "moving"}:
            await motion_gate.stop()
        if motion_gate and motion_gate.task and not motion_gate.task.done():
            await asyncio.shield(motion_gate.task)

    async def motion_startup(_):
        if motion_gate:
            motion_gate.recover()
            app["motion_monitor"] = asyncio.create_task(motion_gate.monitor())

    async def motion_cleanup(_):
        task = app.get("motion_monitor")
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app.router.add_get("/", index)
    app.router.add_get(r"/assets/{file:console\.(js|css)}", asset)
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/maps", maps)
    app.router.add_get("/api/diagnostics", diagnostics)
    app.router.add_post("/api/actions/{action}", action)
    app.router.add_post("/api/motion/{operation}", motion_action)
    app.router.add_get("/planner/socket", bridge, allow_head=False)
    app.router.add_get("/planner/{file:.*}", planner)
    app.router.add_get("/index.html", legacy)
    app.on_shutdown.append(shutdown)
    app.on_startup.append(motion_startup)
    app.on_cleanup.append(motion_cleanup)
    return app


def main():
    parser = argparse.ArgumentParser()
    try:
        if os.getuid() == 0:
            raise ValueError("请使用非 root 账号运行。")
        address = str(ipaddress.IPv4Address(os.environ["ODIN_WEB_BIND"]))
        port = int(os.environ["ODIN_WEB_PORT"])
        tls = None
        cert, key = os.environ.get("ODIN_WEB_TLS_CERT"), os.environ.get("ODIN_WEB_TLS_KEY")
        if bool(cert) != bool(key):
            raise ValueError("TLS 证书和私钥须同时配置。")
        if cert:
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(cert, key)
        origin = f"{'https' if tls else 'http'}://{address}:{port}"
        deployment = Deployment(os.environ)
        app = create_app(deployment, origin, Path(os.environ["ODIN_WEB_PLANNER_ROOT"]))
        threading.Thread(target=deployment.telemetry.run, daemon=True).start()
        web.run_app(app, host=address, port=port, ssl_context=tls, access_log=None,
                    print=lambda _: print(f"在浏览器打开 {origin}/", flush=True))
    except (OSError, ValueError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
