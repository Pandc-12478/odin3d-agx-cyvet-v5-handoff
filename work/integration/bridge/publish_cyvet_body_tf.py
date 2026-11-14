#!/usr/bin/env python3
"""Publish the measured Odin1 mount relative to the Cyvet URDF BASE_LINK."""
import math
import os

import rclpy
from geometry_msgs.msg import TransformStamped
from rclpy.qos import DurabilityPolicy, QoSProfile
from tf2_msgs.msg import TFMessage
from tf2_ros import StaticTransformBroadcaster


def main():
    if os.environ.get("ROS_DOMAIN_ID") != "90":
        raise SystemExit("Set ROS_DOMAIN_ID=90 before publishing the Cyvet body TF.")

    rclpy.init()
    node = rclpy.create_node("odin_cyvet_body_tf")
    try:
        transform = TransformStamped()
        transform.header.stamp = node.get_clock().now().to_msg()
        transform.header.frame_id = "imu"
        transform.child_frame_id = "robot_base_link"
        # Four-hip center, measured from the front-left lower housing corner.
        # Odin faces forward and tilts downward by 8 degrees.
        transform.transform.translation.x = -0.2092913385
        transform.transform.translation.y = 0.02897
        transform.transform.translation.z = -0.2767662714
        half_pitch = math.radians(-8.0) / 2
        transform.transform.rotation.y = math.sin(half_pitch)
        transform.transform.rotation.w = math.cos(half_pitch)

        broadcaster = StaticTransformBroadcaster(node)
        browser_pub = node.create_publisher(
            TFMessage, "/odin_cyvet/body_tf_static",
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )
        broadcaster.sendTransform(transform)
        # ROSBridge may replay only the first message of a shared latched topic.
        browser_pub.publish(TFMessage(transforms=[transform]))
        node.get_logger().info("Publishing imu -> robot_base_link for TF2 and the web.")
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
