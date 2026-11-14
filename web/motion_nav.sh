#!/usr/bin/env bash
# Fixed operations for the Web motion gate. No browser-provided command or path.
set -Eeuo pipefail
WEB_HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NAV="$WEB_HERE/../work/integration/odin_onekey_nav"
HERE="$NAV"
source "$NAV/common.sh"
[[ $# == 1 && ( "$1" == start || "$1" == stop ) ]] || fail '仅支持 start/stop'
operation="$1"
[[ -n "${ODIN_EXPECT_NAV_RUN:-}" ]] || fail '缺少本次导航会话标识'
read_state
[[ "$RUN_DIR" == "$ODIN_EXPECT_NAV_RUN" ]] || fail '导航会话已变化，拒绝运动操作'
require_session_owner "$SESSION" "$RUN_DIR"
load_ros

publish_bool() {
  timeout 8 ros2 topic pub --once --qos-durability volatile "$1" \
    std_msgs/msg/Bool "{data: $2}" >/dev/null
}
latest_sdk_disabled() {
  [[ -f "$LOG_DIR/sdk.log" ]] && tail -n 80 -- "$LOG_DIR/sdk.log" |
    awk '/enabled=(True|False).*SDK参数=/ { last=$0 } END { exit !(last ~ /enabled=False/) }'
}

if [[ "$operation" == stop ]]; then
  # Disable first: the bridge publishes zero in its existing 20 Hz SDK loop.
  delivered=no
  # A failed readiness check can stop before this run ever starts the SDK.
  # In that case there is no SDK disable acknowledgement to wait for.
  if [[ ! -f "$LOG_DIR/sdk.log" ]] && ! window_running sdk && ! node_present /dog_sdk_bridge; then
    delivered=not_started
  fi
  if window_running sdk && publish_bool /dog_bridge/enable false; then
    for ((i=0; i<5; i++)); do
      if latest_sdk_disabled; then delivered=yes; break; fi
      sleep 1
    done
  fi
  if window_running controller; then publish_bool /stop_navigation true || true; fi
  # stop_nav.sh requests bridge shutdown, waits for its exit and restores only this run.
  ODIN_EXPECT_NAV_RUN="$RUN_DIR" bash "$NAV/stop_nav.sh" ||
    fail '停止链未完成；请现场使用物理急停并检查 SDK 日志'
  [[ "$delivered" != no ]] || fail 'SDK 未确认禁用；请现场使用物理急停'
  if [[ "$delivered" == not_started ]]; then
    info '本次 SDK Bridge 未启动，导航会话已退出；请现场确认机器狗状态'
  else
    info 'SDK 已报告 enabled=False 且受控会话退出；现场仍须确认机器狗状态'
  fi
  exit 0
fi

guard_mapping
tmux has-session -t "=$SESSION" 2>/dev/null || fail '导航会话未运行'
window_running controller && window_running sdk || fail '控制器或 SDK Bridge 未就绪'
node_present /real_controller && node_present /dog_sdk_bridge || fail '控制节点未就绪'
check_readiness 2
latest_sdk_disabled || fail 'SDK 尚未报告零速禁用状态'
[[ -f "$LOG_DIR/controller.log" ]] || fail '缺少控制器规划日志'
tail -n 300 -- "$LOG_DIR/controller.log" | awk '
  /Received planned_path with [1-9][0-9]* poses.*Waiting for \/start_navigation confirmation/ { pending=1 }
  /Received empty planned_path|Navigation execution started|Stop navigation requested|Navigation start denied/ { pending=0 }
  END { exit !pending }
' ||
  fail '控制器尚未收到待启动的规划路径，请在网页重新选点规划'
before_controller="$(wc -c < "$LOG_DIR/controller.log")"
before_sdk="$(wc -c < "$LOG_DIR/sdk.log")"
publish_bool /start_navigation true || fail '开始导航请求未送达控制器'
for ((i=0; i<4; i++)); do
  if tail -c +"$((before_controller+1))" -- "$LOG_DIR/controller.log" |
      grep -F 'Navigation execution started with' >/dev/null; then break; fi
  sleep 1
done
tail -c +"$((before_controller+1))" -- "$LOG_DIR/controller.log" |
  grep -F 'Navigation execution started with' >/dev/null ||
  fail '控制器未确认开始，保持 SDK 禁用；请现场核查'
publish_bool /dog_bridge/enable true || fail 'SDK 使能请求未送达；需现场核查'
for ((i=0; i<4; i++)); do
  if tail -c +"$((before_sdk+1))" -- "$LOG_DIR/sdk.log" |
      grep -E 'enabled=True.*SDK参数=' >/dev/null; then
    info 'SDK 已报告 enabled=True，导航执行中'
    exit 0
  fi
  sleep 1
done
fail 'SDK 未确认运动使能；必须立即执行受控停止并现场核查'
