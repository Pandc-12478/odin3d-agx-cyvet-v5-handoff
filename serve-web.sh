#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source "$(dirname -- "${BASH_SOURCE[0]}")/odin3d-settings.sh"
[[ -f "$ODIN3D_BASE/work/install.complete" ]] || { echo '请先由负责人运行 bash install.sh。' >&2; exit 2; }
require_web_access
export ODIN_WEB_BIND
ODIN_WEB_BIND="$(resolve_web_address)" || { echo '无线地址未确认，请核对 config.env。' >&2; exit 2; }
exec 8>"$ODIN3D_BASE/work/web-server.lock"
flock -n 8 || { echo '本部署的 Web 控制台已在运行。' >&2; exit 2; }
set +u
source /opt/ros/humble/setup.bash
source "$ODIN_WS/install/setup.bash"
source "$ODIN_ROOT/nav_ws/install/setup.bash"
set -u
export ROS_DOMAIN_ID=90 ROS_LOCALHOST_ONLY=0
export ODIN_WEB_PLANNER_ROOT
ODIN_WEB_PLANNER_ROOT="$(ros2 pkg prefix --share jie_octomap)/web"
for resource in index.html main.js styles.css vendor/three.module.js vendor/roslib.min.js vendor/jsm/controls/OrbitControls.js; do
  [[ -s "$ODIN_WEB_PLANNER_ROOT/$resource" ]] || { echo "缺少三维页面文件：$resource" >&2; exit 2; }
done
exec /usr/bin/python3 -B "$ODIN3D_BASE/web/server.py"
