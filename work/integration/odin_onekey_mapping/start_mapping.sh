#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
command -v tmux >/dev/null || fail '请先安装 tmux：sudo apt install tmux'
lock_state
[[ -d "$ODIN_WS/install" ]] || fail '缺少 odin_ws/install'
[[ ! -f "$STATE/active.env" ]] || fail "发现未结束的建图会话。先运行 status_mapping.sh 或 stop_mapping.sh"
if tmux has-session -t "$SESSION" 2>/dev/null; then fail "建图 tmux session 已存在"; fi
if tmux has-session -t "$NAV_SESSION" 2>/dev/null; then fail "导航一键启动仍运行。请先安全停止导航并检查机器狗"; fi
[[ ! -e "$ROOT/onekey_nav_state/active" && ! -L "$ROOT/onekey_nav_state/active" ]] ||
  fail '上次导航配置仍待恢复'
load_ros
if check_odin_process; then fail 'Odin1 驱动正在运行，不能同时启动第二份（导航和建图必须分离）'; fi
if ps -eo args | grep -E '[d]og_sdk_bridge.py --execute|[s]tart_real_controller.sh' >/dev/null; then
  fail '检测到实机控制进程。请先安全停止导航，确认机器狗静止'
fi
SOURCE_TARGET="$(realpath -e -- "$SOURCE_CONFIG")" || fail "找不到驱动配置：$SOURCE_CONFIG"
INSTALL_TARGET="$(realpath -e -- "$INSTALL_CONFIG")" || fail "找不到 Launch 配置：$INSTALL_CONFIG"
require_isolated_configs "$SOURCE_TARGET" "$INSTALL_TARGET"
CONFIG_TARGETS=("$SOURCE_TARGET")
if [[ "$INSTALL_TARGET" != "$SOURCE_TARGET" ]]; then
  [[ ! "$INSTALL_TARGET" -ef "$SOURCE_TARGET" ]] || fail '两份配置互为硬链接，无法独立恢复'
  CONFIG_TARGETS+=("$INSTALL_TARGET")
fi
for target in "${CONFIG_TARGETS[@]}"; do
  [[ -f "$target" && -O "$target" && -w "$target" && -w "$(dirname -- "$target")" ]] ||
    fail "配置及所在目录必须允许写入，且配置须归当前用户所有：$target"
  MODE="$(sed -nE 's/^[[:space:]]*custom_map_mode:[[:space:]]*([0-9]+).*/\1/p' "$target")"
  [[ "$MODE" == 2 ]] || fail "预期导航配置 custom_map_mode=2，实际=${MODE:-空}：$target"
done

[[ "$MAP_ROOT" =~ ^/[A-Za-z0-9_./-]+$ ]] || fail '建图目录需要是绝对路径，并且只含英文、数字、点、下划线或连字符'
mkdir -p -- "$MAP_ROOT" "$LOG_ROOT"
DEST="$(mktemp -d -- "$MAP_ROOT/odin_scan_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
STAMP="${DEST##*/odin_scan_}"
LOG="$LOG_ROOT/odin_scan_$STAMP.log"
BACKUP_DIR="$STATE/config_$STAMP"
mkdir -m 700 -- "$BACKUP_DIR"
for i in "${!CONFIG_TARGETS[@]}"; do
  cp -p -- "${CONFIG_TARGETS[i]}" "$BACKUP_DIR/$i.original.yaml"
  cp -p -- "$BACKUP_DIR/$i.original.yaml" "$BACKUP_DIR/$i.mapping.yaml"
  write_mapping_config "$BACKUP_DIR/$i.original.yaml" "$BACKUP_DIR/$i.mapping.yaml"
done
# Publish recovery state before changing either config.
ACTIVE_TMP="$(mktemp -- "$STATE/.active.XXXXXXXX")"
{
  printf 'MENU_OWNER=%q\n' "${ODIN_MENU_OWNER:-}"
  printf 'DEST=%q\n' "$DEST"
  printf 'LOG=%q\n' "$LOG"
  printf 'STAMP=%q\n' "$STAMP"
  printf 'BACKUP_DIR=%q\n' "$BACKUP_DIR"
  printf 'SOURCE_TARGET=%q\n' "$SOURCE_TARGET"
  printf 'INSTALL_TARGET=%q\n' "$INSTALL_TARGET"
} > "$ACTIVE_TMP"
mv -T -- "$ACTIVE_TMP" "$STATE/active.env"
for i in "${!CONFIG_TARGETS[@]}"; do
  cmp -s -- "${CONFIG_TARGETS[i]}" "$BACKUP_DIR/$i.original.yaml" ||
    fail "配置自备份后发生变化，请检查：${CONFIG_TARGETS[i]}"
done
for i in "${!CONFIG_TARGETS[@]}"; do
  install_config "$BACKUP_DIR/$i.mapping.yaml" "${CONFIG_TARGETS[i]}"
done
info "建图配置已备份：$BACKUP_DIR"
info "新地图目录：$DEST"
info '启动 Odin1 SLAM 模式（不会启动机器狗运动控制）'
tmux new-session -d -s "$SESSION" -n odin -e "ODIN3D_ENV=${ODIN3D_ENV:-}" \
  -e "ODIN_RUN_OWNER=$BACKUP_DIR" bash "$HERE/run_mapping_driver.sh" "$LOG" 9>&-
info '等待驱动节点及本次配置加载日志'
found=no
for i in $(seq 1 30); do
  if driver_loaded_mapping && timeout 4 ros2 node list 2>/dev/null | grep -x /host_sdk_sample >/dev/null; then
    found=yes
    break
  fi
  if ! tmux has-session -t "$SESSION" 2>/dev/null; then break; fi
  sleep 1
done
if [[ "$found" != yes ]]; then
  info "未确认驱动就绪或加载本次建图配置。查看：$LOG"
  info "使用 stop_mapping.sh --discard 恢复旧配置（确认没有需要保存的数据后）"
  exit 2
fi
if ss -H -lnt 'sport = :9090' | grep . >/dev/null; then
  fail '实时点云 WebSocket 9090 已被占用，建图会话待恢复'
fi
tmux new-window -d -t "=$SESSION:" -n preview -e "ODIN3D_ENV=$ODIN3D_ENV" \
  bash "$HERE/run_mapping_preview.sh" 9>&-
info '驱动已加载本次建图配置。请先检查 /odin1/odometry 与 /odin1/cloud_slam，再开始慢速采集'
info "采集结束执行：$HERE/save_mapping.sh"
info '全程保持人工监护，不自动使能机器狗行走'
