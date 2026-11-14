#!/usr/bin/env bash
set -e
if [ "${REAL_BASE_FRAME:-}" != robot_base_link ]; then
  echo 'Set REAL_BASE_FRAME=robot_base_link only after measuring the IMU to body transform.' >&2
  exit 1
fi
source /opt/ros/humble/setup.bash
source "${ODIN_ROOT:-$HOME/odin_nav_integration}/nav_ws/install/setup.bash"
export ROS_DOMAIN_ID=90 ROS_LOCALHOST_ONLY=0
exec ros2 run octo_planner d1_controller --ros-args \
  -r __node:=real_controller -p use_sim_time:=false \
  -p map_frame:=map -p base_frame:="$REAL_BASE_FRAME" \
  -p base_frame_candidates:="$REAL_BASE_FRAME" \
  -p robot_center_offset_frame:="$REAL_BASE_FRAME" \
  -p robot_center_offset_x:=0.0 -p robot_center_offset_y:=0.0 -p robot_center_offset_z:=0.0 \
  -p path_topic:=/planned_path -p cmd_vel_topic:=/dog_bridge/cmd_vel \
  -p start_navigation_topic:=/start_navigation \
  -p stop_navigation_topic:=/stop_navigation \
  -p manual_cmd_vel_topic:=/real_controller/manual_cmd_vel_isolated \
  -p require_start_command:=true -p enable_tracking_debug_view:=false \
  -p align_final_yaw:=false \
  -p max_linear_speed:=0.25 -p max_lateral_speed:=0.25 -p max_angular_speed:=0.20
