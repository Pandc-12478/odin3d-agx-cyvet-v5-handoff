#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
lock_state
read_state
if [[ -n "${ODIN_EXPECT_MENU_OWNER:-}" && "${MENU_OWNER:-}" != "$ODIN_EXPECT_MENU_OWNER" ]]; then
  fail '建图会话不属于正在退出的菜单'
fi
if [[ -n "${ODIN_EXPECT_MAPPING_RUN:-}" && "${BACKUP_DIR:-}" != "$ODIN_EXPECT_MAPPING_RUN" ]]; then
  fail '建图会话已变化，请重新核对本次地图'
fi
case "${1:-}" in
  '') ;;
  --discard) ;;
  *) fail '用法：stop_mapping.sh [--discard]' ;;
esac
if [[ "${1:-}" != '--discard' ]]; then
  [[ -s "$RECEIPT" ]] && verified_map "$(cat -- "$RECEIPT")" ||
    fail '没有本次已确认的地图。先执行 save_mapping.sh；明确结束未验证的会话时用 stop_mapping.sh --discard'
fi

if [[ -n "${BACKUP_DIR:-}" ]]; then
  [[ "$(realpath -e -- "$SOURCE_CONFIG")" == "$SOURCE_TARGET" &&
     "$(realpath -e -- "$INSTALL_CONFIG")" == "$INSTALL_TARGET" ]] ||
    fail '配置路径自启动后发生变化，请核对备份和配置'
  CONFIG_TARGETS=("$SOURCE_TARGET")
  if [[ "$INSTALL_TARGET" != "$SOURCE_TARGET" ]]; then CONFIG_TARGETS+=("$INSTALL_TARGET"); fi
  ARCHIVE="$BACKUP_DIR"
else
  # The previous script edited only the installed YAML.
  ARCHIVE="$STATE/completed_$STAMP"
  [[ -f "$STATE/control_command.before_mapping.yaml" ]] || fail '缺少旧会话的安装配置备份'
  [[ ! -L "$INSTALL_CONFIG" && -f "$INSTALL_CONFIG" ]] || fail "旧会话的安装配置路径已变化：$INSTALL_CONFIG"
  CONFIG_TARGETS=("$INSTALL_CONFIG")
  mkdir -p -m 700 -- "$ARCHIVE"
  cp -p -- "$STATE/control_command.before_mapping.yaml" "$ARCHIVE/0.original.yaml"
  write_mapping_config "$ARCHIVE/0.original.yaml" "$ARCHIVE/0.mapping.yaml"
fi
require_isolated_configs "${CONFIG_TARGETS[@]}"
for i in "${!CONFIG_TARGETS[@]}"; do
  [[ -s "$ARCHIVE/$i.original.yaml" && -s "$ARCHIVE/$i.mapping.yaml" ]] ||
    fail "缺少配置备份：${CONFIG_TARGETS[i]}"
done

info '请求结束建图并恢复配置'
if tmux has-session -t "=$SESSION" 2>/dev/null; then
  require_session_owner "$SESSION" "${BACKUP_DIR:-}"
  if tmux list-panes -t "=$SESSION:preview" >/dev/null 2>&1; then
    tmux send-keys -t "=$SESSION:preview" C-c
  fi
  tmux send-keys -t "=$SESSION:odin" C-c
  for i in $(seq 1 15); do
    if ! tmux has-session -t "=$SESSION" 2>/dev/null; then break; fi
    sleep 1
  done
  if tmux has-session -t "=$SESSION" 2>/dev/null; then
    fail "Odin 驱动尚未退出。拒绝恢复配置以免运行中的配置混淆。请检查 tmux attach -t $SESSION"
  fi
fi
# Guard against an Odin driver outside the managed tmux session.
load_ros
if check_odin_process; then fail '仍检测到 Odin 驱动进程；停止全部驱动后再恢复导航配置'; fi
# Check every target before restoring any of them.
for i in "${!CONFIG_TARGETS[@]}"; do
  target="${CONFIG_TARGETS[i]}"
  if ! cmp -s -- "$ARCHIVE/$i.original.yaml" "$target" &&
     ! cmp -s -- "$ARCHIVE/$i.mapping.yaml" "$target"; then
    fail "配置出现会话外的修改，保留当前文件及备份：$target"
  fi
done
for i in "${!CONFIG_TARGETS[@]}"; do
  target="${CONFIG_TARGETS[i]}"
  if ! cmp -s -- "$ARCHIVE/$i.original.yaml" "$target"; then
    install_config "$ARCHIVE/$i.original.yaml" "$target"
  fi
  info "已核对原导航配置：$target"
done
if [[ -z "${BACKUP_DIR:-}" && -f "$RECEIPT" ]]; then
  cp -p -- "$RECEIPT" "$ARCHIVE/last_saved_map.txt"
fi
mv -T -- "$STATE/active.env" "$ARCHIVE/active.env"
if [[ -z "${BACKUP_DIR:-}" ]]; then
  rm -f -- "$STATE/control_command.before_mapping.yaml" "$RECEIPT"
fi
info "本次目录及其中的文件已保留：$DEST"
if [[ -s "$ARCHIVE/last_saved_map.txt" ]]; then info "保存文件：$(cat "$ARCHIVE/last_saved_map.txt")"; fi
info "导航配置备份：$ARCHIVE"
