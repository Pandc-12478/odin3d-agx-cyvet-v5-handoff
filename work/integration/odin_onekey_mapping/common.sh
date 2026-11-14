#!/usr/bin/env bash
set -Eeuo pipefail
source "$HERE/../odin3d-settings.sh"
ROOT="$ODIN_ROOT"
STATE="$ROOT/onekey_mapping_state"
MAP_ROOT="$ODIN_MAP_ROOT"
LOG_ROOT="$ROOT/onekey_mapping_logs"
SOURCE_CONFIG="$ODIN_WS/src/odin_ros_driver/config/control_command.yaml"
INSTALL_CONFIG="$ODIN_WS/install/odin_ros_driver/share/odin_ros_driver/config/control_command.yaml"
SESSION="odin_mapping"
NAV_SESSION="odin_real_nav"
info(){ echo "[建图] $*"; }
fail(){ echo "[错误] $*" >&2; exit 1; }
load_ros(){
  set +u
  source /opt/ros/humble/setup.bash
  source "$ODIN_WS/install/setup.bash"
  export ROS_DOMAIN_ID=90 ROS_LOCALHOST_ONLY=0
  set -u
}
lock_state(){
  mkdir -p "$STATE"
  exec 9>"$STATE/session.lock"
  flock -n 9 || fail '另一个建图命令正在运行'
}
read_state(){
  [[ -f "$STATE/active.env" ]] || fail '没有活动建图会话'
  source "$STATE/active.env"
  RECEIPT="${BACKUP_DIR:-$STATE}/last_saved_map.txt"
}
check_odin_process(){
  if pgrep -x host_sdk_sample >/dev/null; then return 0; fi
  if timeout 8 ros2 node list 2>/dev/null | grep -x /host_sdk_sample >/dev/null; then return 0; fi
  pgrep -f '[r]os2 launch odin_ros_driver odin1_ros2.launch.py' >/dev/null
}
driver_loaded_mapping(){
  [[ -f "$LOG" ]] || return 1
  awk -v config="$SOURCE_CONFIG" -v dest="$DEST" '
    /^\[host_sdk_sample-[0-9]+\] / {
      sub(/^\[host_sdk_sample-[0-9]+\] /, "")
      sub(/\r$/, "")
      if ($0 == "Loading config file: " config) loaded = 1
      if ($0 == "custom_map_mode = 1") mapping = 1
      if ($0 == "Loaded key: mapping_result_dest_dir = " dest) destination = 1
    }
    END { exit !(loaded && mapping && destination) }
  ' "$LOG"
}
write_mapping_config(){
  python3 - "$1" "$2" "$DEST" <<'PY'
from pathlib import Path
import re
import sys

original, output = map(Path, sys.argv[1:3])
dest = sys.argv[3]
if not re.fullmatch(r'/[A-Za-z0-9_./-]+', dest):
    raise SystemExit(f'Unsupported map output path: {dest}')
text = original.read_text()
for key, value in (
    ('custom_map_mode', '1'),
    ('mapping_result_dest_dir', dest),
    ('mapping_result_file_name', "''"),
):
    pattern = rf'(?m)^([ \t]*{key}:[ \t]*).*$'
    text, count = re.subn(pattern, lambda m: m.group(1) + value, text)
    if count != 1:
        raise SystemExit(f'{key} not unique or missing in {original}')
output.write_text(text)
PY
}
install_config(){
  local from="$1" target="$2" tmp
  tmp="$(mktemp -- "$(dirname -- "$target")/.control_command.XXXXXXXX")" ||
    fail "无法在配置目录创建临时文件：$target"
  if ! cp -p -- "$from" "$tmp" || ! mv -fT -- "$tmp" "$target"; then
    rm -f -- "$tmp"
    fail "写入配置失败：$target"
  fi
}
verified_map(){
  local map="$1" name
  [[ "$map" == "$DEST/"* ]] || return 1
  name="${map#"$DEST/"}"
  [[ "$name" =~ ^map_[0-9]{8}_[0-9]{6}\.bin$ && ! -L "$map" && -f "$map" && -s "$map" ]]
}
