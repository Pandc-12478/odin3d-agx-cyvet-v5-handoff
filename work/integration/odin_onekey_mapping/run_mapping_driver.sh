#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../odin3d-settings.sh"
LOG="${1:?必须传入日志路径}"
set +u
source /opt/ros/humble/setup.bash
source "$ODIN_WS/install/setup.bash"
set -u
export ROS_DOMAIN_ID=90 ROS_LOCALHOST_ONLY=0
exec > >(tee -a "$LOG") 2>&1
echo "==== Mapping driver started: $(date -Is) ===="
exec ros2 launch odin_ros_driver odin1_ros2.launch.py
