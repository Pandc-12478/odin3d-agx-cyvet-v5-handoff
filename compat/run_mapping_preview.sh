#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
set +u
source /opt/ros/humble/setup.bash
source "$ODIN_WS/install/setup.bash"
set -u
export ROS_DOMAIN_ID=90 ROS_LOCALHOST_ONLY=0
exec ros2 run rosbridge_server rosbridge_websocket --ros-args \
  -r __node:=rosbridge_websocket -p address:=127.0.0.1 -p port:=9090
