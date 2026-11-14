#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
lock_state
read_state
[[ -z "${ODIN_EXPECT_MAPPING_RUN:-}" || "${BACKUP_DIR:-}" == "$ODIN_EXPECT_MAPPING_RUN" ]] ||
  fail '建图会话已变化，请刷新状态'
[[ -n "${BACKUP_DIR:-}" ]] || fail '旧会话未加载本次输出目录。确认旧地图已保存后，执行 stop_mapping.sh --discard，再重新建图'
if [[ -s "$RECEIPT" ]]; then
  CAND="$(cat -- "$RECEIPT")"
  verified_map "$CAND" || fail "已确认的地图缺失或不完整：$CAND"
  info "本次地图已确认保存：$CAND"
  exit 0
fi
driver_loaded_mapping || fail "驱动日志未确认本次建图模式和输出目录：$LOG"
tmux has-session -t "=$SESSION" 2>/dev/null || fail '建图进程不在运行'
require_session_owner "$SESSION" "$BACKUP_DIR"

PENDING="$BACKUP_DIR/save_log_offset"
if [[ ! -f "$PENDING" ]]; then
  load_ros
  SERVICE="$(timeout 8 ros2 service type /odin1/save_map 2>/dev/null | tail -n 1)" || fail '查询 SaveMap 服务失败'
  [[ "$SERVICE" == 'odin_ros_driver/srv/SaveMap' ]] || fail "SaveMap 服务类型不匹配：${SERVICE:-无}"
  EXISTING="$(find "$DEST" -maxdepth 1 -name '*.bin' -print -quit)"
  [[ -z "$EXISTING" ]] || fail "本次目录已有 BIN 文件，拒绝发送可能覆盖文件的请求：$EXISTING"
  OFFSET="$(stat -c '%s' -- "$LOG")"
  OFFSET_TMP="$(mktemp -- "$BACKUP_DIR/.save_log_offset.XXXXXXXX")"
  printf '%s\n' "$OFFSET" > "$OFFSET_TMP"
  mv -T -- "$OFFSET_TMP" "$PENDING"
  info '请求 Odin1 保存地图'
  if RESPONSE="$(timeout 30 ros2 service call /odin1/save_map odin_ros_driver/srv/SaveMap '{value: 1}' 2>&1)"; then
    printf '%s\n' "$RESPONSE"
    if grep -Eq 'success=False|success: false' <<< "$RESPONSE"; then
      rm -f -- "$PENDING"
      fail '驱动拒绝了保存请求'
    fi
  else
    printf '%s\n' "$RESPONSE"
    info '未收到服务确认，继续检查传输日志和文件'
  fi
else
  OFFSET="$(cat -- "$PENDING")"
  info '继续检查上一次保存请求'
fi
[[ "$OFFSET" =~ ^[0-9]+$ ]] || fail "无效的日志偏移：$PENDING"

info '等待 BIN 传输完成'
LAST='' STABLE=0
for ((i = 0; i < 180; i++)); do
  [[ "$(stat -c '%s' -- "$LOG")" -ge "$OFFSET" ]] || fail "驱动日志已被截断：$LOG"
  CAND="$(tail -c "+$((OFFSET + 1))" -- "$LOG" | awk -v dest="$DEST/" '
    /^\[host_sdk_sample-[0-9]+\].*\[map_transfer\]: Map transfer completed: / {
      sub(/^.*\[map_transfer\]: Map transfer completed: /, "")
      sub(/\r$/, "")
      if (index($0, dest) == 1 && substr($0, length(dest) + 1) ~ /^map_[0-9_]+\.bin$/) path = $0
    }
    END { if (path != "") print path }
  ')"
  if verified_map "$CAND" && SIGNATURE="$(stat -c '%d:%i:%s:%y' -- "$CAND")"; then
    if [[ -z "$LAST" ]]; then info '正在核对 BIN 文件大小是否稳定'; fi
    if [[ "$CAND:$SIGNATURE" == "$LAST" ]]; then
      STABLE=$((STABLE + 1))
    else
      LAST="$CAND:$SIGNATURE"
      STABLE=0
    fi
    if (( STABLE >= 4 )); then
      sync -- "$CAND" "$DEST"
      if verified_map "$CAND" && [[ "$(stat -c '%d:%i:%s:%y' -- "$CAND")" == "$SIGNATURE" ]]; then
        RECEIPT_TMP="$(mktemp -- "$BACKUP_DIR/.last_saved_map.XXXXXXXX")"
        printf '%s\n' "$CAND" > "$RECEIPT_TMP"
        mv -T -- "$RECEIPT_TMP" "$RECEIPT"
        info "地图已落盘且大小稳定，驱动已确认传输完成：$CAND"
        info "运行 $HERE/stop_mapping.sh 恢复导航配置"
        exit 0
      fi
      LAST='' STABLE=0
    fi
  else
    LAST='' STABLE=0
  fi
  sleep 2
done
info "未确认传输完成。查看 $LOG，可再次运行本脚本继续验证同一请求"
exit 2
