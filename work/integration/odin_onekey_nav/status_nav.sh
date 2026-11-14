#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/common.sh"
if [[ -f "$ACTIVE/session.env" ]]; then
  read_state
  info "BIN：$MAP_BIN"
  info "规划地图包：$MAP_PACKAGE"
  info "日志：$LOG_DIR"
  if localization_log_ready; then info '本次驱动日志已报告重定位'; fi
fi
load_ros
if [[ "${1:-}" == '--check' ]]; then
  [[ -n "${RUN_DIR:-}" ]] || fail '没有可验证的导航会话'
  check_readiness 2
  exit 0
fi
printf '\n== tmux 进程窗口 ==\n'
if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux list-windows -t "$SESSION" -F '#{window_name} #{pane_dead} #{pane_current_command}'
else
  echo '未发现一键启动 session'
fi
printf '\n== ROS 节点 ==\n'
timeout 8 ros2 node list 2>/dev/null | grep -E 'host_sdk_sample|odin_cyvet_body_tf|real_controller|rosbridge_websocket|jie_path_node|map_package_manager' || true
printf '\n== 端口 ==\n'
ss -lntp | grep -E ':8080|:9090' || true
printf '\n== STOP 发布者 ==\n'
timeout 8 ros2 topic info /dog_bridge/stop -v 2>/dev/null | grep -E 'Publisher count|Node name:' || true
printf '\n== 控制器限速 ==\n'
for p in max_linear_speed max_lateral_speed max_angular_speed; do
  printf '%s: ' "$p"
  timeout 6 ros2 param get /real_controller "$p" 2>&1 || true
done
printf '\n== 近期 SDK 日志 ==\n'
tail -n 14 "$LOG_DIR/sdk.log" 2>/dev/null || true
