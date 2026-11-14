#!/usr/bin/env bash
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common.sh"
source "$HERE/map_package.sh"
prepare_only=no
convert_only=no
case "${1:-}" in
  --list) [[ $# == 1 ]] || fail '用法：start_nav.sh --list'; list_maps; exit 0 ;;
  --convert) [[ $# == 2 ]] || fail '用法：start_nav.sh --convert /path/to/map.bin'; prepare_only=yes; convert_only=yes; shift ;;
  --prepare) [[ $# == 2 ]] || fail '用法：start_nav.sh --prepare /path/to/map.bin'; prepare_only=yes; shift ;;
  --*) fail '用法：start_nav.sh [--list | --convert BIN | --prepare BIN | BIN]' ;;
esac
(( $# <= 1 )) || fail '请只指定一个 BIN'
select_map "${1:-}"
command -v tmux >/dev/null || fail '需要 tmux'
lock_state
guard_mapping
[[ -d "$NAV_WS/install" ]] || fail '缺少 nav_ws/install'
load_ros
if [[ "$prepare_only" == yes ]]; then
  [[ ! -L "$ACTIVE" && ! -e "$ACTIVE" ]] || fail '请先停止导航再处理地图'
  prepare_package "$convert_only"
  exit 0
fi
[[ ! -e "$ACTIVE" && ! -L "$ACTIVE" ]] || fail "上次导航尚未恢复配置。请先运行 $HERE/stop_nav.sh"
if tmux has-session -t "=$SESSION" 2>/dev/null; then fail '一键导航已运行'; fi
if odin_running || sdk_running; then fail '发现正在运行的 Odin 或 SDK 进程'; fi
[[ -f "$BRIDGE/dog_sdk_bridge.py" && -f "$BRIDGE/start_real_controller.sh" ]] || fail '缺少现有控制脚本'
WEB_ADDRESS="$(resolve_web_address)" || fail '无线网卡无可用地址，请检查 config.env'
active="$(timeout 8 ros2 node list)" || fail '无法检查当前 ROS 节点'
if grep -Ex '/(host_sdk_sample|odin_cyvet_body_tf|real_controller|dog_sdk_bridge|rosbridge_websocket|jie_path_node|map_package_manager)' <<< "$active" >/dev/null; then
  fail '发现已有导航节点，请先安全停止原会话'
fi
if ss -H -lnt 'sport = :9090' | grep . >/dev/null; then fail 'WebSocket 端口 9090 已被占用'; fi
[[ "$(curl -fsS --max-time 3 "http${ODIN_WEB_TLS_CERT:+s}://$WEB_ADDRESS:$ODIN_WEB_PORT/api/health" ${ODIN_WEB_TLS_CERT:+--insecure})" == odin3d-console ]] ||
  fail '请先启动本交付的 Web 控制台'
if pgrep -f '[r]os2 topic pub .*/dog_bridge/stop' >/dev/null; then fail '发现残留的 SDK 停止发布进程'; fi

info '准备导航地图包'
prepare_package
info '启动重定位服务'
SOURCE_TARGET="$(realpath -e -- "$SOURCE_CONFIG")" || fail "找不到驱动配置：$SOURCE_CONFIG"
INSTALL_TARGET="$(realpath -e -- "$INSTALL_CONFIG")" || fail "找不到 Launch 配置：$INSTALL_CONFIG"
require_isolated_configs "$SOURCE_TARGET" "$INSTALL_TARGET"
config_targets
RUN_DIR="$(mktemp -d -- "$NAV_STATE/nav_$(date +%Y%m%d_%H%M%S)_XXXXXX")"
LOG_DIR="$RUN_DIR/logs"
mkdir -- "$LOG_DIR"
web_template="$(ros2 pkg prefix --share octo_planner)/config/nav_params.yaml"
[[ -s "$web_template" ]] || fail "找不到现有 Web 配置：$web_template"
/usr/bin/python3 - "$web_template" "$RUN_DIR/web_nav_params.yaml" "$MAP_BIN" "$MAP_PCD" "$MAP_PACKAGE" <<'PY'
from pathlib import Path
import sys
import yaml

source, output, bin_path, pcd_path, package = sys.argv[1:]
config = yaml.safe_load(Path(source).read_text())
config.update(relocalization_bin_file=bin_path, relocalization_pcd_file=pcd_path, map_package_dir=package)
Path(output).write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
PY
for i in "${!CONFIG_TARGETS[@]}"; do
  target="${CONFIG_TARGETS[i]}"
  [[ -f "$target" && -O "$target" && -w "$target" && -w "$(dirname -- "$target")" ]] ||
    fail "配置必须可由当前用户安全替换：$target"
  cp -p -- "$target" "$RUN_DIR/$i.original.yaml"
  cp -p -- "$target" "$RUN_DIR/$i.navigation.yaml"
  /usr/bin/python3 - "$RUN_DIR/$i.navigation.yaml" "$MAP_BIN" <<'PY'
import json
from pathlib import Path
import re
import sys
import yaml

path = Path(sys.argv[1])
text = path.read_text()
expected = yaml.safe_load(text)
expected['register_keys'].update(custom_map_mode=2, relocalization_map_abs_path=sys.argv[2])
for key, value in (
    ('custom_map_mode', '2'),
    ('relocalization_map_abs_path', json.dumps(sys.argv[2], ensure_ascii=False)),
):
    text, count = re.subn(rf'(?m)^([ \t]*{key}:[ \t]*).*$', lambda m: m[1] + value, text)
    if count != 1:
        raise SystemExit(f'Expected one {key} in {path}')
if yaml.safe_load(text) != expected:
    raise SystemExit(f'Unexpected YAML changes in {path}')
path.write_text(text)
PY
done
[[ "$(stat -c '%d:%i:%s:%y:%z' -- "$MAP_BIN")" == "$MAP_SIGNATURE" ]] ||
  fail '所选 BIN 在启动前发生变化'
# Publish the recovery state before replacing either file.
{
  MENU_OWNER="${ODIN_MENU_OWNER:-}"
  for name in MENU_OWNER WEB_ADDRESS RUN_DIR LOG_DIR MAP_BIN MAP_PACKAGE MAP_ID SOURCE_TARGET INSTALL_TARGET; do
    printf '%s=%q\n' "$name" "${!name}"
  done
} > "$RUN_DIR/session.env"
ln -sT -- "$RUN_DIR" "$ACTIVE"
for i in "${!CONFIG_TARGETS[@]}"; do
  cmp -s -- "${CONFIG_TARGETS[i]}" "$RUN_DIR/$i.original.yaml" ||
    fail "配置自备份后发生变化：${CONFIG_TARGETS[i]}"
done
for i in "${!CONFIG_TARGETS[@]}"; do
  install_config "$RUN_DIR/$i.navigation.yaml" "${CONFIG_TARGETS[i]}"
done

info "启动重定位：$MAP_BIN"
info "运行日志：$LOG_DIR"
info "停止与恢复：$HERE/stop_nav.sh"
if ! start_component odin; then
  if ! odin_running && ! tmux has-session -t "=$SESSION" 2>/dev/null; then restore_configs; fi
  fail 'Odin tmux 窗口启动失败'
fi
start_component tf
wait_for 20 node_present /host_sdk_sample || fail "Odin ROS 节点未出现：$LOG_DIR/odin.log"
start_component web
info '正在等待网页加载地图包'
wait_for 35 web_package_loaded || fail "Web 未确认自动加载所选地图包：$LOG_DIR/web.log"
# The controller is path-only until an explicit start command; SDK is still absent.
start_component controller
wait_for 12 node_present /real_controller || fail "受控规划器未就绪：$LOG_DIR/controller.log"
info "Web 导航：http${ODIN_WEB_TLS_CERT:+s}://$WEB_ADDRESS:$ODIN_WEB_PORT"
info '正在验证定位与机体位姿'
check_readiness 45
info "现场核对位姿、同层规划路径和急停条件后，运行 $HERE/arm_nav.sh"
