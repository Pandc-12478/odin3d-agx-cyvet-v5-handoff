#!/usr/bin/env python3
"""AGX 宇泛机器狗：持续连接的六方向交互控制。每次限时运动后清零，quit 时结束会话。"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import signal
import select
import sys
import time

ZERO = {"lineVelocityX": 0.0, "lineVelocityY": 0.0, "velocity": 0.0}
DIRECTIONS = {
    "forward": ("lineVelocityX", 1, "前进"),
    "backward": ("lineVelocityX", -1, "后退"),
    "left": ("lineVelocityY", 1, "左移"),
    "right": ("lineVelocityY", -1, "右移"),
    "turn-left": ("velocity", 1, "左转"),
    "turn-right": ("velocity", -1, "右转"),
}
stopping = False


def on_signal(signum, frame):
    global stopping
    stopping = True


def check_interrupt():
    if stopping:
        raise InterruptedError("收到中断，进入清理流程")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="interactive", choices=["interactive", "status"])
    parser.add_argument("--iface", default="eno1")
    parser.add_argument("--device-id", default=os.environ.get("DOG_DEVICE_ID", ""))
    parser.add_argument("--speed", type=float, default=0.1, help="平移速度大小，单位 m/s，默认 0.1；方向由子命令决定")
    parser.add_argument("--turn-speed", type=float, default=0.2, help="转向角速度大小，单位 rad/s，默认 0.2")
    parser.add_argument("--duration", type=float, default=1.0, help="运动秒数，默认 1")
    parser.add_argument("--ready-delay", type=float, default=2.0, help="walking 状态连续稳定等待秒数，默认 2，最少 2")
    parser.add_argument("--lib-dir", default=str(Path.home() / "uniubi_robot_sdk_py/.deps/uniubi_robot_sdk/lib/aarch64_host"))
    args = parser.parse_args()
    if not math.isfinite(args.speed) or not 0 < args.speed <= 0.3:
        parser.error("speed 必须在 (0, 0.3] 内")
    if not math.isfinite(args.turn_speed) or not 0 < args.turn_speed <= 0.3:
        parser.error("turn-speed 必须在 (0, 0.3] rad/s 内")
    if not math.isfinite(args.duration) or not 0 < args.duration <= 10:
        parser.error("duration 必须在 (0, 10] 秒内")
    if not math.isfinite(args.ready_delay) or not 2 <= args.ready_delay <= 10:
        parser.error("ready-delay 必须在 [2, 10] 秒内")
    if not args.device_id.strip():
        parser.error("device-id 不得为空")
    return args


def load_sdk(args):
    # 动态链接器需要在 Python 启动前读取此路径，因此先重启当前进程一次。
    lib_dir = str(Path(args.lib_dir).expanduser().resolve())
    if not (Path(lib_dir) / "librobotMotionSdk.so").is_file():
        raise RuntimeError(f"找不到 SDK 库：{lib_dir}/librobotMotionSdk.so；请用 --lib-dir 指定")
    paths = os.environ.get("LD_LIBRARY_PATH", "").split(":")
    if paths[0] != lib_dir:
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = ":".join([lib_dir] + [p for p in paths if p and p != lib_dir])
        os.execve(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], env)
    import robot_motion_sdk as sdk
    return sdk


def require_call(client, ok, label):
    if not ok:
        raise RuntimeError(f"{label}失败：{client.get_last_error()}")


def ensure_control(client, sdk):
    check_interrupt()
    if client.get_state() != sdk.HighLevelState.kControlled:
        raise RuntimeError("控制权已丢失，结束会话；不会自动重新取权或恢复运动")


def read_console_command(client, sdk):
    # 用原始字节读取，避免一次粘贴多行时 TextIO 缓冲与 select 不一致。
    print("dog> ", end="", flush=True)
    data = bytearray()
    oversized = False
    while True:
        ensure_control(client, sdk)
        if not select.select([sys.stdin], [], [], 0.1)[0]:
            continue
        chunk = os.read(sys.stdin.fileno(), 1)
        if not chunk:
            return None  # Ctrl+D / SSH 输入流关闭：进入统一清理
        if chunk == b"\n":
            if oversized:
                raise ValueError("命令过长，已丢弃")
            return data.decode("utf-8", errors="replace").strip()
        if len(data) < 512:
            data.extend(chunk)
        else:
            oversized = True


def print_help():
    print("""命令（一次输入一条，方向以机器狗自身为准）：
  forward [速度 秒数]      前进，默认 0.1 m/s、1 秒
  backward [速度 秒数]     后退，默认 0.1 m/s、1 秒
  left [速度 秒数]         左移，默认 0.1 m/s、1 秒
  right [速度 秒数]        右移，默认 0.1 m/s、1 秒
  turn-left [角速度 秒数]  左转，默认 0.2 rad/s、1 秒
  turn-right [角速度 秒数] 右转，默认 0.2 rad/s、1 秒
  zero / stop             清零速度，保持 walking 和控制权
  state                   查询运动状态
  help                    显示帮助
  quit                    清零、请求停止动作、释放控制权并退出
运动中 Ctrl+C 会结束整个会话并尝试清理。不要一次粘贴多条运动命令。
运动结束只清零，不退出 walking；等待输入时可能继续原地踏步。
退出沿用已实测的停止/释放流程，不额外发送 laying；最终姿态需现场确认。
""", flush=True)


def timed_move(args, sdk, client, direction, speed, duration):
    ensure_control(client, sdk)
    state = client.query_motion_state()
    if not isinstance(state, dict) or state.get("action") != "walking":
        raise RuntimeError(f"当前动作不是 walking，不发送运动速度：{state}")
    axis, sign, label = DIRECTIONS[direction]
    params = {**ZERO, axis: sign * speed}
    try:
        require_call(client, client.set_action_params(params), "设置运动速度")
        unit = "rad/s" if axis == "velocity" else "m/s"
        print(f"[运行] {label} {speed:g} {unit}，持续 {duration:g} 秒", flush=True)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            ensure_control(client, sdk)
            time.sleep(0.02)
    finally:
        # 中断或报错时也立即尝试清零；失败会结束会话进入外层清理。
        require_call(client, client.set_action_params(ZERO.copy()), "清零速度")
    print("[完成] 已清零速度，保持 walking，等待下一条命令", flush=True)


def run_console(args, sdk, client):
    print_help()
    while True:
        try:
            line = read_console_command(client, sdk)
        except ValueError as error:
            print(f"[输入错误] {error}")
            continue
        if line is None:
            print("\n[退出] 输入流已关闭")
            return
        parts = line.split()
        if not parts:
            continue
        command = parts[0].lower()
        if command in ("quit", "exit") and len(parts) == 1:
            return
        if command == "help" and len(parts) == 1:
            print_help()
        elif command in ("zero", "stop") and len(parts) == 1:
            ensure_control(client, sdk)
            require_call(client, client.set_action_params(ZERO.copy()), "清零速度")
            print("[完成] 速度已清零，保持 walking")
        elif command == "state" and len(parts) == 1:
            value = client.query_motion_state()
            if value is None:
                raise RuntimeError(f"状态查询失败：{client.get_last_error()}")
            print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        elif command in DIRECTIONS:
            try:
                if len(parts) not in (1, 3):
                    raise ValueError("格式：方向，或 方向 速度 秒数；例如 left 0.1 1")
                speed = (args.turn_speed if command.startswith("turn-") else args.speed) if len(parts) == 1 else float(parts[1])
                duration = args.duration if len(parts) == 1 else float(parts[2])
                if not math.isfinite(speed) or not 0 < speed <= 0.3:
                    raise ValueError("速度大小必须在 (0, 0.3] 内，方向由命令决定")
                if not math.isfinite(duration) or not 0 < duration <= 10:
                    raise ValueError("时长必须在 (0, 10] 秒内")
            except ValueError as error:
                print(f"[输入错误] {error}")
                continue
            timed_move(args, sdk, client, command, speed, duration)
        else:
            print("[输入错误] 未知命令或参数数量错误，输入 help 查看")


def run(args, sdk):
    initialized = False
    client = None
    control_attempted = False
    action_attempted = False
    result = 0

    def require(ok, operation):
        if not ok:
            raise RuntimeError(f"{operation}失败：{client.get_last_error()}")

    def cleanup_call(label, call):
        nonlocal result
        try:
            if not call():
                raise RuntimeError(str(client.get_last_error()))
            print(f"[清理] {label}成功", flush=True)
        except Exception as error:
            result = result or 1
            print(f"[警告] {label}失败：{error}；请现场通过 App/遥控器确认机器人状态", file=sys.stderr, flush=True)

    try:
        sdk.service.set_network_interface(args.iface)
        if not sdk.service.initial(None, "agx-dog-command"):
            raise RuntimeError("SDK 初始化失败")
        initialized = True
        check_interrupt()
        client = sdk.MotionHighLevelClient(device_id=args.device_id)
        require(client.connect(lease_ms=60000), "连接")
        print(f"[成功] 已连接，指定网卡 {args.iface}", flush=True)
        deadline = time.monotonic() + 10
        while True:
            check_interrupt()
            caps = client.get_motion_capabilities()
            if caps is not None:
                break
            if client.get_last_error() != sdk.HighLevelError.kRpcConnectFailed or time.monotonic() >= deadline:
                raise RuntimeError(f"动作能力查询失败：{client.get_last_error()}")
            time.sleep(0.2)

        if args.command == "status":
            for name, query in [("system", client.query_system_status), ("motion", client.query_motion_state)]:
                check_interrupt()
                value = query()
                if value is None:
                    raise RuntimeError(f"{name} 查询失败：{client.get_last_error()}")
                print(name + ": " + json.dumps(value, ensure_ascii=False, indent=2, default=str))
            print("[说明] motion={} 表示未取得有效运动状态，不能用来确认姿态。")
        else:
            print("[前提] 遥控器已断连、App 已退出控制页；机器狗稳定趴好，运动方向及四周空旷且网线留有余量。", flush=True)
            check_interrupt()
            control_attempted = True
            require(client.start_control(timeout_ms=10000), "获取控制权")
            deadline = time.monotonic() + 10
            while client.get_state() != sdk.HighLevelState.kControlled:
                check_interrupt()
                if time.monotonic() >= deadline:
                    raise RuntimeError("等待控制权超时")
                time.sleep(0.05)
            check_interrupt()
            action_attempted = True
            require(client.start_action("walking", ZERO.copy()), "启动零速度行走模式")
            print("[等待] 确认零速 walking 并等待动作切换稳定；尚未发送运动速度", flush=True)
            deadline = time.monotonic() + 15 + args.ready_delay
            stable_since = None
            while True:
                check_interrupt()
                if client.get_state() != sdk.HighLevelState.kControlled:
                    raise RuntimeError("等待行走就绪时失去控制权")
                state = client.query_motion_state()
                ready = (isinstance(state, dict) and state.get("action") == "walking"
                         and all(state.get(key) == 0 for key in ZERO))
                now = time.monotonic()
                if ready:
                    if stable_since is None:
                        stable_since = now
                    if now - stable_since >= args.ready_delay:
                        break
                else:
                    stable_since = None
                if now >= deadline:
                    raise RuntimeError(f"等待零速 walking 超时，不发送运动速度；最后状态={state}")
                time.sleep(0.1)
            print("[就绪] 已确认零速 walking；该状态不等于姿态传感器验证", flush=True)
            run_console(args, sdk, client)
    except InterruptedError as error:
        print(f"[中断] {error}", file=sys.stderr)
        result = 130
    except Exception as error:
        print(f"[失败] {error}", file=sys.stderr)
        result = 1
    finally:
        # 各项清理独立尝试，上一项失败不会跳过后续步骤。
        if client is not None:
            if action_attempted:
                cleanup_call("清零速度", lambda: client.set_action_params(ZERO.copy()))
                cleanup_call("请求停止动作", client.stop_action)
            if control_attempted:
                try:
                    if client.get_state() == sdk.HighLevelState.kControlled:
                        cleanup_call("释放控制权", client.release_control)
                    else:
                        print("[清理] 当前未持有控制权，继续断开客户端", flush=True)
                except Exception as error:
                    print(f"[警告] 检查控制权失败：{error}", file=sys.stderr)
                    result = result or 1
            try:
                client.disconnect()
            except Exception as error:
                print(f"[警告] 断开连接失败：{error}", file=sys.stderr)
                result = result or 1
        if initialized:
            try:
                sdk.service.shutdown()
            except Exception as error:
                print(f"[警告] SDK 关闭失败：{error}", file=sys.stderr)
                result = result or 1
    return result


def main():
    args = parse_args()
    try:
        sdk = load_sdk(args)
    except Exception as error:
        print(f"[失败] SDK 加载失败：{error}", file=sys.stderr)
        return 1
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    return run(args, sdk)


if __name__ == "__main__":
    raise SystemExit(main())
