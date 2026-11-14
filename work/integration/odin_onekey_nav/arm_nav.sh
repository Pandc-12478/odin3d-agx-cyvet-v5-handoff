#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
web_approved=no
if [[ $# == 1 && "$1" == --web-approved && -n "${ODIN_WEB_APPROVAL_RUN:-}" ]]; then
  web_approved=yes
elif [[ $# != 0 || ! -t 0 ]]; then
  fail '请在现场终端交互确认，或由受控 Web 后端确认'
fi
read_state
reviewed_run="$RUN_DIR"
info "当前地图：$MAP_BIN"
info '请先在网页确认实际位姿、同层路径、周围空间和物理急停。下一步会接管 SDK 并进入零速 walking'
if [[ "$web_approved" == yes ]]; then
  [[ "$ODIN_WEB_APPROVAL_RUN" == "$reviewed_run" ]] || fail '网页确认的导航会话已变化'
  info '已收到受控 Web 后端的现场负责人确认'
else
  read -r -p '现场条件已确认后输入 READY：' answer
  [[ "$answer" == READY ]] || fail '未确认现场条件'
fi
lock_state
guard_mapping
read_state
[[ "$RUN_DIR" == "$reviewed_run" ]] || fail '导航会话已变化，请重新核对现场'
tmux has-session -t "=$SESSION" 2>/dev/null || fail '导航 tmux 会话未运行'
require_session_owner "$SESSION" "$RUN_DIR"
require_vendor_access || fail '请先检查厂商授权与 SDK'
[[ -n "$DOG_DEVICE_ID" ]] || fail '请负责人在私有环境设置 DOG_DEVICE_ID'
load_ros
check_readiness 2

if ! window_running controller; then
  if node_present /real_controller; then fail '已有外部 real_controller'; fi
  start_component controller
fi
wait_for 12 node_present /real_controller || fail "控制器未就绪：$LOG_DIR/controller.log"

sdk_ready() {
  [[ -f "$LOG_DIR/sdk.log" ]] &&
    tail -n 30 -- "$LOG_DIR/sdk.log" |
      awk '/enabled=(True|False).*SDK参数=/ { last = $0 } END { exit !(last ~ /enabled=False/) }' &&
    node_present /dog_sdk_bridge
}
if ! window_running sdk; then
  if sdk_running || node_present /dog_sdk_bridge; then fail '已有外部 SDK Bridge'; fi
  # The bridge must report disabled for this launch, including after a restart.
  if [[ -f "$LOG_DIR/sdk.log" ]]; then
    mv -- "$LOG_DIR/sdk.log" "$(mktemp -- "$LOG_DIR/sdk.previous.XXXXXXXX")"
  fi
  start_component sdk
fi
wait_for 20 sdk_ready || fail "SDK 未确认零速就绪：$LOG_DIR/sdk.log"
info 'SDK 已报告 enabled=False；请在 Web 核对规划路径，再独立点击开始导航'
