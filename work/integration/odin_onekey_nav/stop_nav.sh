#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
lock_state
guard_mapping
if [[ -e "$ACTIVE" || -L "$ACTIVE" ]]; then
  read_state
  [[ -z "${ODIN_EXPECT_NAV_RUN:-}" || "$RUN_DIR" == "$ODIN_EXPECT_NAV_RUN" ]] ||
    fail '导航会话已变化，请刷新状态'
  if [[ -n "${ODIN_EXPECT_MENU_OWNER:-}" && "${MENU_OWNER:-}" != "$ODIN_EXPECT_MENU_OWNER" ]]; then
    fail '导航会话不属于正在退出的菜单'
  fi
  if tmux has-session -t "=$SESSION" 2>/dev/null; then
    require_session_owner "$SESSION" "$RUN_DIR"
  fi
elif ! tmux has-session -t "=$SESSION" 2>/dev/null; then
  info '没有一键导航会话或待恢复的配置'
  [[ -z "${ODIN_EXPECT_NAV_RUN:-}" ]] || fail '导航会话已变化，请刷新状态'
  exit 0
else
  fail '发现没有本部署恢复记录的导航会话，请联系负责人'
fi
load_ros
send_once() {
  if ! timeout 8 ros2 topic pub --once --qos-durability volatile "$1" \
      std_msgs/msg/Bool "{data: $2}" >/dev/null 2>&1; then
    info "未确认停止消息送达：$1"
  fi
}
if window_running controller; then send_once /stop_navigation true; fi
if window_running sdk; then
  send_once /dog_bridge/enable false
  send_once /dog_bridge/stop true
fi
sdk_stopped() {
  ! sdk_running && ! window_running sdk
}
wait_for 18 sdk_stopped ||
  fail 'SDK Bridge 未退出。请现场确认机器狗停止并检查 SDK 日志，保留会话和配置'

for component in controller web tf odin; do
  if window_running "$component"; then
    tmux send-keys -t "=$SESSION:$component" C-c
  fi
done
session_stopped() {
  ! odin_running && ! tmux has-session -t "=$SESSION" 2>/dev/null
}
wait_for 20 session_stopped ||
  fail '导航会话或 Odin 驱动尚未退出，保留配置。请检查 tmux 和日志'
if [[ -n "${RUN_DIR:-}" ]]; then
  active_nodes="$(timeout 8 ros2 node list --no-daemon --spin-time 2)" || fail '无法核对驱动是否已退出'
  if grep -Ex '/(host_sdk_sample|dog_sdk_bridge|real_controller)' <<< "$active_nodes" >/dev/null; then
    fail '仍发现 Odin 或控制节点，保留配置以便安全恢复'
  fi
  restore_configs
fi
info '导航已停止，地图文件保留'
