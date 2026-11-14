#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/common.sh"
component="${1:-}"
LOG_DIR="${2:-$LOG_DIR}"
load_ros
mkdir -p "$LOG_DIR"
exec > >(tee -a "${LOG_DIR}/${component}.log") 2>&1
printf '\n==== %s | %s ====\n' "$component" "$(date -Is)"
case "$component" in
  odin) exec ros2 launch odin_ros_driver odin1_ros2.launch.py ;;
  tf) exec /usr/bin/python3 "$BRIDGE/publish_cyvet_body_tf.py" ;;
  web)
    if [[ -f "$LOG_DIR/../web_nav_params.yaml" ]]; then
      export ODIN_WEB_NAV_CONFIG="$LOG_DIR/../web_nav_params.yaml"
    fi
    export ODIN_ACTIVE_WEB_ADDRESS=127.0.0.1
    exec ros2 launch octo_planner web_test.launch.py \
      launch_controller:=false launch_planner:=true launch_web:=false \
      launch_rosbridge:=true launch_rviz:=false ;;
  controller) export REAL_BASE_FRAME=robot_base_link
      exec bash "$BRIDGE/start_real_controller.sh" ;;
  sdk)
      : "${DOG_DEVICE_ID:?请在私有环境设置设备 ID}"
      exec /usr/bin/python3 "$BRIDGE/dog_sdk_bridge.py" --execute --navigation \
        --base-frame robot_base_link --iface "$DOG_IFACE" --device-id "$DOG_DEVICE_ID" \
        --source "$DOG_CONTROL_SOURCE" --lib-dir "$DOG_SDK_LIB_DIR" ;;
  *) fail "未知组件: $component" ;;
esac
