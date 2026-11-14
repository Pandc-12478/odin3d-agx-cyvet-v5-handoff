#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
load_ros
printf '\n== 建图状态 ==\n'
if [[ -f "$STATE/active.env" ]]; then
  read_state; echo "输出目录：$DEST"; echo "日志：$LOG";
else echo '无活动建图会话'; fi
printf '\n== 驱动窗口 ==\n'
tmux list-windows -t "$SESSION" 2>/dev/null || echo '未发现建图 tmux session'
printf '\n== 当前模式 ==\n'
for config in "$SOURCE_CONFIG" "$INSTALL_CONFIG"; do
  printf '%s\n' "$config"
  grep -nE '^ *custom_map_mode:|^ *mapping_result_dest_dir:|^ *mapping_result_file_name:' "$config" || true
done
printf '\n== ROS 节点 ==\n'
timeout 8 ros2 node list 2>/dev/null | grep -E 'host_sdk_sample|real_controller|rosbridge_websocket' || true
printf '\n== SaveMap 服务 ==\n'
timeout 8 ros2 service type /odin1/save_map 2>/dev/null || true
if [[ -n "${RECEIPT:-}" && -f "$RECEIPT" ]]; then
 printf '\n== 本次保存 ==\n'; cat "$RECEIPT"; fi
if [[ -n "${DEST:-}" && -d "$DEST" ]]; then
 printf '\n== BIN 文件 ==\n'; find "$DEST" -maxdepth 1 -name '*.bin' -type f -printf '%f %s bytes\n'; fi
