#!/usr/bin/env python3
"""Build and save a jie_3d_nav package in an isolated ROS domain."""
import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import rclpy
from jie_map_msgs.srv import SaveNavigationMapPackage
from octomap_msgs.msg import Octomap
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pcd", type=Path)
    parser.add_argument("package", type=Path)
    parser.add_argument("--min-points", type=int, default=8)
    parser.add_argument("--map-id", default="odin_20261111_160421_r020")
    args = parser.parse_args()
    if os.environ.get("ROS_DOMAIN_ID") != "78" or os.environ.get("ROS_LOCALHOST_ONLY") != "1":
        parser.error("Use isolated ROS_DOMAIN_ID=78 and ROS_LOCALHOST_ONLY=1")
    if not args.pcd.is_file() or args.package.exists() or args.min_points < 1:
        parser.error("Require an existing PCD, new package path and positive min-points")

    evidence = args.package.parent
    processes = []
    logs = []
    rclpy.init()
    node = rclpy.create_node("new_map_builder")
    try:
        commands = [
            ("pcd", "jie_octomap", "pcd_to_octomap_node", [
                "-p", f"pcd_file:={args.pcd}", "-p", "resolution:=0.2",
                "-p", f"min_points_per_voxel:={args.min_points}",
                "-p", "min_cluster_voxels:=1",
            ]),
            ("planner", "octo_planner", "jie_path_node", [
                "-p", "robot_radius:=0.3", "-p", "require_ground_support:=true",
                "-p", "strict_direct_ground_support:=true",
                "-p", "snap_search_radius_cells:=8",
                "-p", f"map_id:={args.map_id}",
            ]),
            ("manager", "jie_octomap", "map_package_manager", []),
        ]
        for label, package, executable, parameters in commands:
            log = (evidence / f"build_{label}.log").open("w")
            logs.append(log)
            processes.append(subprocess.Popen(
                ["ros2", "run", package, executable, "--ros-args", *parameters],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            ))
        client = node.create_client(
            SaveNavigationMapPackage, "/map_package_manager/save_package"
        )
        if not client.wait_for_service(timeout_sec=30):
            raise RuntimeError("Map package service unavailable")
        # The PCD node republishes the full map every second. The planner needs
        # much longer to preprocess it; stop the offline publisher after all
        # subscribers have received one map so export can finish.
        map_received = [False]
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        subscription = node.create_subscription(
            Octomap, "/octomap", lambda _msg: map_received.__setitem__(0, True), qos
        )
        receive_deadline = time.monotonic() + 180
        while not map_received[0] and time.monotonic() < receive_deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("Offline map builder exited before publishing OctoMap")
            rclpy.spin_once(node, timeout_sec=1)
        if not map_received[0]:
            raise RuntimeError("Offline OctoMap was not published within 180 seconds")
        time.sleep(2)
        if processes[0].poll() is None:
            os.killpg(processes[0].pid, signal.SIGINT)
            processes[0].wait(timeout=15)
        node.destroy_subscription(subscription)
        print("Offline OctoMap received; stopped repeated PCD publishing", flush=True)

        deadline = time.monotonic() + 300
        result = None
        while time.monotonic() < deadline:
            request = SaveNavigationMapPackage.Request()
            request.package_path = str(args.package)
            request.overwrite = False
            future = client.call_async(request)
            rclpy.spin_until_future_complete(node, future, timeout_sec=80)
            if not future.done():
                client.remove_pending_request(future)
            else:
                result = future.result()
                if result and result.success:
                    break
                if result and result.message not in (
                    "octomap not ready", "octomap message not received yet",
                    "preblocked marker not received yet",
                    "traversable marker not received yet",
                    "risk cost cloud not received yet",
                    "planner export service timed out",
                ):
                    raise RuntimeError(f"Map package save failed: {result.message}")
            time.sleep(2)
        if result is None or not result.success:
            raise RuntimeError(f"Map package save failed: {getattr(result, 'message', 'timeout')}")
        print(json.dumps({
            "pcd": str(args.pcd), "package": str(args.package),
            "min_points_per_voxel": args.min_points,
            "manifest": result.manifest_path,
        }), flush=True)
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
        for log in logs:
            log.close()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
