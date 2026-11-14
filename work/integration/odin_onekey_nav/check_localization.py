#!/usr/bin/env python3
"""Check graph endpoints and advancing Odin odometry and body TF."""
from pathlib import Path
import sys
import time

from nav_msgs.msg import Odometry
import numpy as np
from octomap_msgs.msg import Octomap
import rclpy
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))
from localization_watchdog import ProgressWatch


def stamp_ns(stamp):
    return stamp.sec * 1_000_000_000 + stamp.nanosec


def pose_values(position, rotation):
    return (position.x, position.y, position.z,
            rotation.x, rotation.y, rotation.z, rotation.w)


def main():
    expected_nodes = {
        "host_sdk_sample", "odin_cyvet_body_tf", "jie_path_node",
        "map_package_manager", "rosbridge_websocket",
    }
    expected_publishers = {
        "/odin1/odometry": "host_sdk_sample",
        "/tf": "host_sdk_sample",
        "/tf_static": "odin_cyvet_body_tf",
        "/odin_cyvet/body_tf_static": "odin_cyvet_body_tf",
        "/octomap": "map_package_manager",
    }
    with np.load(sys.argv[1], allow_pickle=False) as package:
        expected_map = (
            str(package["frame_id"][0]), str(package["octomap_id"][0]),
            bool(package["binary"][0]), float(package["resolution"][0]),
        )
        expected_data = package["data"]
    rclpy.init()
    node = rclpy.create_node("onekey_nav_readiness")
    buffer = Buffer()
    listener = TransformListener(buffer, node)
    odom_watch, tf_watch = ProgressWatch(), ProgressWatch()

    def on_odometry(msg):
        pose = msg.pose.pose
        odom_watch.update(stamp_ns(msg.header.stamp), time.monotonic(),
                          pose_values(pose.position, pose.orientation))

    map_matches = False

    def on_octomap(msg):
        nonlocal map_matches
        header = (msg.header.frame_id, msg.id, msg.binary, msg.resolution)
        map_matches = header == expected_map and np.array_equal(msg.data, expected_data)

    node.create_subscription(Odometry, "/odin1/odometry", on_odometry, qos_profile_sensor_data)
    latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(Octomap, "/octomap", on_octomap, latched)
    deadline = time.monotonic() + 12
    ready_since = None
    missing_nodes, missing_topics = expected_nodes, set(expected_publishers)
    tf_error = "未接收 TF"
    try:
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.05)
            now = time.monotonic()
            missing_nodes = expected_nodes - set(node.get_node_names())
            missing_topics = {
                topic for topic, publisher in expected_publishers.items()
                if publisher not in {info.node_name for info in node.get_publishers_info_by_topic(topic)}
            }
            try:
                tf = buffer.lookup_transform("map", "robot_base_link", Time())
                transform = tf.transform
                tf_watch.update(stamp_ns(tf.header.stamp), now,
                                pose_values(transform.translation, transform.rotation))
                tf_error = ""
            except TransformException as error:
                tf_error = str(error)
            if (not missing_nodes and not missing_topics and map_matches and not tf_error
                    and odom_watch.fresh(now) and tf_watch.fresh(now)):
                if ready_since is None:
                    ready_since = now
                elif now - ready_since >= 1:
                    return 0
            else:
                ready_since = None
        print(f"缺少节点：{sorted(missing_nodes)}；缺少话题发布者：{sorted(missing_topics)}",
              file=sys.stderr)
        print(f"在线 OctoMap 与所选包一致：{map_matches}；里程计连续更新：{odom_watch.fresh(time.monotonic())}；"
              f"机体 TF 连续更新：{tf_watch.fresh(time.monotonic())}；{tf_error}",
              file=sys.stderr)
        return 1
    finally:
        listener.unregister()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
