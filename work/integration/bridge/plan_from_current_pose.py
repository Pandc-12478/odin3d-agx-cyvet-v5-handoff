#!/usr/bin/env python3
"""Publish measured current start and a user-specified goal; never starts motion."""
import argparse
import math
import os
import time
import rclpy
from geometry_msgs.msg import PointStamped, PoseStamped
from rclpy.qos import QoSProfile, DurabilityPolicy
from tf2_ros import Buffer, TransformListener
from localization_watchdog import ProgressWatch


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-frame', required=True)
    p.add_argument('--goal', nargs=4, type=float, required=True, metavar=('X', 'Y', 'Z', 'YAW_RAD'))
    a = p.parse_args()
    if os.environ.get('ROS_DOMAIN_ID') != '90' or not all(math.isfinite(v) for v in a.goal):
        p.error('Require domain 90 and finite goal coordinates')
    rclpy.init()
    n = rclpy.create_node('real_plan_request')
    buf = Buffer()
    listener = TransformListener(buf, n)
    qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    start = n.create_publisher(PointStamped, '/real/start_point', qos)
    goal = n.create_publisher(PoseStamped, '/real/goal_pose', qos)
    watch = ProgressWatch()
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            rclpy.spin_once(n, timeout_sec=0.05)
            try:
                tf = buf.lookup_transform('map', a.base_frame, rclpy.time.Time())
            except Exception:
                continue
            t = tf.transform.translation
            watch.update(tf.header.stamp.sec * 10**9 + tf.header.stamp.nanosec,
                         time.monotonic(), (t.x, t.y, t.z))
            if watch.fresh(time.monotonic()) and start.get_subscription_count() and goal.get_subscription_count():
                break
        else:
            raise RuntimeError('No advancing real TF or no planner subscribers; no request published')
        s = PointStamped()
        s.header.frame_id = 'map'
        s.header.stamp = tf.header.stamp
        s.point.x, s.point.y, s.point.z = t.x, t.y, t.z
        g = PoseStamped()
        g.header = s.header
        g.pose.position.x, g.pose.position.y, g.pose.position.z = a.goal[:3]
        g.pose.orientation.z = math.sin(a.goal[3] / 2)
        g.pose.orientation.w = math.cos(a.goal[3] / 2)
        start.publish(s)
        # Planner handles start first, then goal, on separate subscriptions.
        for _ in range(10):
            rclpy.spin_once(n, timeout_sec=0.05)
        goal.publish(g)
        for _ in range(20):
            rclpy.spin_once(n, timeout_sec=0.05)
        print(f'Published real start=({t.x}, {t.y}, {t.z}) and goal={a.goal}; motion not enabled')
    finally:
        n.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
