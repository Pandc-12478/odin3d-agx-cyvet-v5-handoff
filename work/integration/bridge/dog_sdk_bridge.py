#!/usr/bin/env python3
"""ROS2→宇泛 SDK 桥接。默认仅检查导入；--execute 才连接并进入零速 walking。

依赖 AGX 上已经测试的 dog_control3.py，不修改该文件。
执行域固定为 90。默认输入 /dog_bridge/cmd_vel；--web-manual 选用独立手动话题。两种模式均通过 /dog_bridge/stop 退出。
控制权丢失、SDK 调用失败或动作状态异常：退出并沿用原脚本清理。
断流超过 0.5 秒：清零并取消启用，必须重新 enable 后发送新速度。
进程崩溃、RPC 卡住或网络断开时，本进程无法保证机器狗收到零速。
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace

ZERO = {"lineVelocityX": 0.0, "lineVelocityY": 0.0, "velocity": 0.0}


def convert(values):
    """values: linear xyz + angular xyz；平移合速度≤0.1，转向≤0.2。"""
    if len(values) != 6 or not all(math.isfinite(v) for v in values):
        raise ValueError("输入包含无效数值")
    vx, vy, vz, wx, wy, wz = values
    if any(abs(v) > 1e-9 for v in (vz, wx, wy)):
        raise ValueError("不支持高度、横滚或俯仰速度")
    speed = math.hypot(vx, vy)
    if speed > 0.25:
        vx, vy = vx / speed * 0.25, vy / speed * 0.25
    return dict(lineVelocityX=vx, lineVelocityY=vy,
                velocity=max(-0.20, min(0.20, wz)))


class CommandGate:
    def __init__(self):
        self.enabled = False
        self.since = None
        self.last_rx = None
        self.command = ZERO.copy()

    def disable(self):
        self.enabled = False
        self.last_rx = None
        self.command = ZERO.copy()

    def enable(self, now):
        self.disable()
        self.enabled = True
        self.since = now

    def receive(self, values, now):
        if not self.enabled:
            return
        try:
            self.command = convert(values)
            self.last_rx = now
        except ValueError:
            self.disable()
            raise

    def output(self, now):
        if not self.enabled:
            return ZERO.copy()
        reference = self.last_rx if self.last_rx is not None else self.since
        if now - reference >= 0.5:
            self.disable()
            return ZERO.copy()
        return self.command.copy()


def run_ros_console(original, sdk, client, opts):
    import rclpy
    from rclpy.node import Node
    from rclpy.signals import SignalHandlerOptions
    from geometry_msgs.msg import Twist
    from std_msgs.msg import Bool

    from localization_watchdog import ProgressWatch
    from nav_msgs.msg import Odometry
    from tf2_ros import Buffer, TransformListener

    class Bridge(Node):
        def __init__(self):
            super().__init__("dog_sdk_bridge")
            self.gate = CommandGate()
            self.exit_requested = False
            self.odom_watch = ProgressWatch()
            self.tf_watch = ProgressWatch()
            if opts.navigation:
                self.tf_buffer = Buffer()
                self.tf_listener = TransformListener(self.tf_buffer, self)
                self.subs_odom = self.create_subscription(
                    Odometry, "/odin1/odometry", self.odometry, rclpy.qos.qos_profile_sensor_data)
            web_manual = getattr(opts, "web_manual", False)
            cmd_topic = "/dog_bridge/manual_cmd_vel" if web_manual else "/dog_bridge/cmd_vel"
            enable_topic = "/dog_bridge/manual_enable" if web_manual else "/dog_bridge/enable"
            self.subs = [
                self.create_subscription(Twist, cmd_topic, self.receive, 1),
                self.create_subscription(Bool, enable_topic, self.enable, 1),
                self.create_subscription(Bool, "/dog_bridge/stop", self.stop, 1),
            ]
            self.get_logger().info(
                "实机桥接就绪，零速 walking；尚未启用速度。"
                " enable=true 后请在0.5秒内开始连续发布速度。")

        def odometry(self, msg):
            p, q = msg.pose.pose.position, msg.pose.pose.orientation
            stamp = msg.header.stamp.sec * 1000000000 + msg.header.stamp.nanosec
            self.odom_watch.update(stamp, time.monotonic(),
                                   (p.x, p.y, p.z, q.x, q.y, q.z, q.w))

        def localization_ok(self):
            if not opts.navigation:
                return True
            now = time.monotonic()
            try:
                tf = self.tf_buffer.lookup_transform("map", opts.base_frame, rclpy.time.Time())
                t, q = tf.transform.translation, tf.transform.rotation
                stamp = tf.header.stamp.sec * 1000000000 + tf.header.stamp.nanosec
                self.tf_watch.update(stamp, now, (t.x, t.y, t.z, q.x, q.y, q.z, q.w))
            except Exception:
                return False
            return self.odom_watch.fresh(now) and self.tf_watch.fresh(now)

        def receive(self, msg):
            values = (msg.linear.x, msg.linear.y, msg.linear.z,
                      msg.angular.x, msg.angular.y, msg.angular.z)
            try:
                self.gate.receive(values, time.monotonic())
            except ValueError as error:
                self.get_logger().error(f"{error}；清零并取消启用")

        def enable(self, msg):
            if msg.data:
                if not self.localization_ok():
                    self.get_logger().error("拒绝启用：真实 odometry/TF 尚未连续更新")
                    return
                if not self.gate.enabled:
                    self.gate.enable(time.monotonic())
                    self.get_logger().info("已启用，等待新的速度消息")
            else:
                self.gate.disable()
                self.get_logger().info("已取消启用，保持零速 walking")

        def stop(self, msg):
            if msg.data:
                self.gate.disable()
                self.exit_requested = True

    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    node = None
    try:
        node = Bridge()
        next_send = time.monotonic()
        next_state = next_send
        last_report = next_send
        while rclpy.ok():
            original.ensure_control(client, sdk)
            rclpy.spin_once(node, timeout_sec=0.01)
            if node.exit_requested:
                raise InterruptedError("收到 /dog_bridge/stop，结束会话")
            now = time.monotonic()
            localization_ok = node.localization_ok()
            if node.gate.enabled and not localization_ok:
                node.gate.disable()
                node.get_logger().error("定位更新失效：清零并取消启用，必须人工重新启用")
            if now >= next_send:
                original.ensure_control(client, sdk)
                was_enabled = node.gate.enabled
                params = node.gate.output(now)
                if was_enabled and not node.gate.enabled:
                    node.get_logger().warn("速度断流：清零并取消启用；不会自动恢复")
                rpc_start = time.monotonic()
                original.require_call(client, client.set_action_params(params, timeout_ms=200), "发送桥接速度")
                rpc_elapsed = time.monotonic() - rpc_start
                if rpc_elapsed >= 0.25:
                    raise RuntimeError(f"SDK速度RPC耗时{rpc_elapsed:.3f}s，结束会话")
                next_send = time.monotonic() + 0.05
                if now - last_report >= 1.0:
                    node.get_logger().info(f"enabled={node.gate.enabled} SDK参数={params} set_rpc_ms={rpc_elapsed*1000:.1f}")
                    last_report = now
            if now >= next_state:
                state = client.query_motion_state(timeout_ms=200)
                if not isinstance(state, dict) or state.get("action") != "walking":
                    raise RuntimeError(f"动作不再是 walking，结束会话：{state}")
                next_state = time.monotonic() + 0.5
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="连接实机，获取控制权并进入零速 walking")
    parser.add_argument("--iface", default="eno1")
    parser.add_argument("--device-id", default=os.environ.get("DOG_DEVICE_ID", ""))
    parser.add_argument("--source", default=str(Path.home() / "agx_dog_control/dog_control3.py"))
    parser.add_argument("--lib-dir", default=str(Path.home() / "uniubi_robot_sdk_py/.deps/uniubi_robot_sdk/lib/aarch64_host"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--web-manual", action="store_true", help="仅订阅网页手动速度，独立使能")
    mode.add_argument("--navigation", action="store_true", help="要求真实 odometry 和 map→机体 TF 连续更新")
    parser.add_argument("--base-frame", help="已完成实测外参的真实机体参考帧")
    opts = parser.parse_args()
    if opts.navigation and not opts.base_frame:
        parser.error("导航模式必须明确 --base-frame，不假设未知外参")
    if opts.execute and os.environ.get("ROS_DOMAIN_ID") != "90":
        parser.error("实机模式请设置 ROS_DOMAIN_ID=90，与模拟域88隔离")
    source = Path(opts.source).expanduser().resolve()
    lib_dir = Path(opts.lib_dir).expanduser().resolve()
    if not source.is_file() or not (lib_dir / "librobotMotionSdk.so").is_file():
        raise RuntimeError("原控制脚本或 SDK 动态库不存在，请检查 --source / --lib-dir")

    paths = os.environ.get("LD_LIBRARY_PATH", "").split(":")
    if paths[0] != str(lib_dir):
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = ":".join([str(lib_dir)] + [p for p in paths if p and p != str(lib_dir)])
        os.execve(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], env)

    import rclpy
    from rclpy.signals import SignalHandlerOptions
    from geometry_msgs.msg import Twist
    from std_msgs.msg import Bool
    import robot_motion_sdk as sdk

    spec = importlib.util.spec_from_file_location("tested_dog_control", source)
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    for name in ("run", "require_call", "ensure_control", "on_signal"):
        if not callable(getattr(original, name, None)):
            raise RuntimeError(f"原脚本缺少接口：{name}")
    print(f"加载检查通过：Python={sys.executable}，原脚本={source}", flush=True)
    if not opts.execute:
        print("仅完成导入检查；没有初始化 SDK、连接机器狗或发送动作。", flush=True)
        return 0

    print("实机模式：即将沿用原脚本获取控制权并进入零速 walking。", flush=True)
    original.stopping = False
    original.run_console = lambda args, sdk_module, client: run_ros_console(original, sdk_module, client, opts)
    signal.signal(signal.SIGINT, original.on_signal)
    signal.signal(signal.SIGTERM, original.on_signal)
    args = SimpleNamespace(iface=opts.iface, device_id=opts.device_id,
                           ready_delay=2.0, command="interactive")
    return original.run(args, sdk)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"[失败] {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

