#!/usr/bin/env bash
set -Eeuo pipefail
source "$HERE/../odin3d-settings.sh"
ROOT="$ODIN_ROOT"
BRIDGE="${ROOT}/bridge"
NAV_WS="${ROOT}/nav_ws"
SESSION="odin_real_nav"
SOURCE_CONFIG="$ODIN_WS/src/odin_ros_driver/config/control_command.yaml"
INSTALL_CONFIG="$ODIN_WS/install/odin_ros_driver/share/odin_ros_driver/config/control_command.yaml"
MAP_ROOT="$ODIN_MAP_ROOT"
MAPPING_STATE="$ROOT/onekey_mapping_state"
NAV_STATE="$ROOT/onekey_nav_state"
ACTIVE="$NAV_STATE/active"
LOG_DIR="$ROOT/onekey_nav_logs"
load_ros() {
  # ROS2 setup.bash 与 nounset 不完全兼容
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  source "$ODIN_WS/install/setup.bash"
  source "${NAV_WS}/install/setup.bash"
  export ROS_DOMAIN_ID=90 ROS_LOCALHOST_ONLY=0
  set -u
}
fail() { printf '错误：%s\n' "$*" >&2; exit 1; }
info() { printf '[导航] %s\n' "$*"; }

lock_state() {
  mkdir -p -- "$MAPPING_STATE" "$NAV_STATE"
  exec 9>"$MAPPING_STATE/session.lock"
  flock -n 9 || fail '另一个建图或导航命令正在操作配置'
}
guard_mapping() {
  if [[ -e "$MAPPING_STATE/active.env" ]] || tmux has-session -t '=odin_mapping' 2>/dev/null; then
    fail '发现未结束的建图会话。请先使用建图停止脚本完成恢复'
  fi
}
read_state() {
  [[ -f "$ACTIVE/session.env" ]] || fail '没有本脚本启动的导航会话'
  source "$ACTIVE/session.env"
}
odin_running() {
  pgrep -x host_sdk_sample >/dev/null ||
    pgrep -f '[r]os2 launch odin_ros_driver odin1_ros2.launch.py' >/dev/null
}
sdk_running() {
  pgrep -f '^(/usr/bin/)?python3 .*/dog_sdk_bridge\.py --execute' >/dev/null
}
window_running() {
  tmux list-panes -t "${SESSION}:$1" -F '#{pane_dead}' 2>/dev/null | grep -Fx 0 >/dev/null
}
start_component() {
  local component="$1"
  if [[ "$component" == odin ]]; then
    tmux new-session -d -s "$SESSION" -n odin \
      -e "ODIN3D_ENV=${ODIN3D_ENV:-}" \
      -e "ODIN_RUN_OWNER=$RUN_DIR" bash "$HERE/run_component.sh" odin "$LOG_DIR" 9>&-
  else
    require_session_owner "$SESSION" "$RUN_DIR"
    local -a env_args=(-e "ODIN3D_ENV=$ODIN3D_ENV")
    if [[ "$component" == sdk ]]; then env_args+=(-e "DOG_DEVICE_ID=$DOG_DEVICE_ID"); fi
    tmux new-window -d -t "=$SESSION:" -n "$component" "${env_args[@]}" bash "$HERE/run_component.sh" "$component" "$LOG_DIR" 9>&-
  fi
}
wait_for() {
  local attempts="$1" i
  shift
  for ((i = 0; i < attempts; i++)); do
    if "$@"; then return 0; fi
    sleep 1
  done
  return 1
}
node_present() {
  timeout 5 ros2 node list 2>/dev/null | grep -Fx "$1" >/dev/null
}
web_package_loaded() {
  [[ -f "$LOG_DIR/web.log" ]] &&
    grep -F "autoloaded map package: $MAP_PACKAGE map_id=$MAP_ID" "$LOG_DIR/web.log" >/dev/null
}
localization_log_ready() {
  [[ -f "$LOG_DIR/odin.log" ]] || return 1
  awk -v config="$SOURCE_CONFIG" -v map="$MAP_BIN" '
    /^\[host_sdk_sample-[0-9]+\] / {
      sub(/^\[host_sdk_sample-[0-9]+\] /, "")
      sub(/\r$/, "")
      if ($0 == "Loading config file: " config) loaded = 1
      if ($0 == "Loaded key: relocalization_map_abs_path = " map) selected = 1
      if ($0 == "custom_map_mode = 2") mode = 1
      if (/\[device_cb\]: Relocalization map set successfully$/) map_set = 1
      if (/\[odom\]: relocalization success!$/) localized = 1
    }
    END { exit !(loaded && selected && mode && map_set && localized) }
  ' "$LOG_DIR/odin.log"
}
check_readiness() {
  local wait="${1:-15}"
  web_package_loaded || fail "Web 未确认加载本次地图包：$LOG_DIR/web.log"
  wait_for 6 curl --fail --silent --output /dev/null --max-time 3 ${ODIN_WEB_TLS_CERT:+--insecure} "http${ODIN_WEB_TLS_CERT:+s}://$WEB_ADDRESS:$ODIN_WEB_PORT/api/health" ||
    fail 'Web 入口未响应'
  ss -H -lnt 'sport = :9090' | awk -v address="127.0.0.1:9090" '$4 == address { found=1 } END { exit !found }' || fail 'WebSocket 9090 未监听'
  wait_for "$wait" localization_log_ready ||
    fail "尚未确认驱动加载所选 BIN 并报告重定位：$LOG_DIR/odin.log"
  /usr/bin/python3 "$HERE/check_localization.py" "$MAP_PACKAGE/octomap_msg.npz" ||
    fail '定位数据链未就绪，请检查日志与话题'
  info '驱动已报告重定位，里程计与 map → robot_base_link TF 连续更新。请在 Web 核对实际位姿和路径'
}
config_targets() {
  CONFIG_TARGETS=("$SOURCE_TARGET")
  if [[ "$INSTALL_TARGET" != "$SOURCE_TARGET" ]]; then
    [[ ! "$INSTALL_TARGET" -ef "$SOURCE_TARGET" ]] || fail '配置为硬链接，无法独立替换'
    CONFIG_TARGETS+=("$INSTALL_TARGET")
  fi
}
install_config() {
  local from="$1" target="$2" tmp
  tmp="$(mktemp -- "$(dirname -- "$target")/.control_command.XXXXXXXX")"
  if ! cp -p -- "$from" "$tmp" || ! mv -fT -- "$tmp" "$target"; then
    rm -f -- "$tmp"
    fail "写入配置失败，原件保留于 $RUN_DIR：$target"
  fi
}
restore_configs() {
  [[ "$(realpath -e -- "$SOURCE_CONFIG")" == "$SOURCE_TARGET" &&
     "$(realpath -e -- "$INSTALL_CONFIG")" == "$INSTALL_TARGET" ]] ||
    fail "配置路径已变化，保留备份：$RUN_DIR"
  require_isolated_configs "$SOURCE_TARGET" "$INSTALL_TARGET"
  config_targets
  for i in "${!CONFIG_TARGETS[@]}"; do
    local target="${CONFIG_TARGETS[i]}"
    [[ -s "$RUN_DIR/$i.original.yaml" && -s "$RUN_DIR/$i.navigation.yaml" ]] ||
      fail "缺少配置备份：$RUN_DIR"
    if ! cmp -s -- "$RUN_DIR/$i.original.yaml" "$target" &&
       ! cmp -s -- "$RUN_DIR/$i.navigation.yaml" "$target"; then
      fail "配置出现会话外的修改，请核对当前文件和备份：$target"
    fi
  done
  for i in "${!CONFIG_TARGETS[@]}"; do
    if ! cmp -s -- "$RUN_DIR/$i.original.yaml" "${CONFIG_TARGETS[i]}"; then
      install_config "$RUN_DIR/$i.original.yaml" "${CONFIG_TARGETS[i]}"
    fi
  done
  unlink "$ACTIVE"
  info "已恢复本次启动前的配置。备份与日志：$RUN_DIR"
}
