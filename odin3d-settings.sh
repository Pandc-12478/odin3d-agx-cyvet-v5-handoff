#!/usr/bin/env bash
# Checkout entry points load this file through the symlink installed by the wizard.
ODIN3D_BASE="$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")" || exit 2
export ODIN3D_ENV="${ODIN3D_ENV:-$ODIN3D_BASE/config.env}"
if [[ -e "$ODIN3D_ENV" || -L "$ODIN3D_ENV" ]]; then
  [[ -f "$ODIN3D_ENV" && -O "$ODIN3D_ENV" && -r "$ODIN3D_ENV" && ! -L "$ODIN3D_ENV" &&
     "$(stat -c %a -- "$ODIN3D_ENV")" == 600 ]] ||
    { echo '私有 config.env 须由当前用户持有，权限设为 600' >&2; exit 2; }
  source "$ODIN3D_ENV" || exit 2
fi
export ODIN_ROOT="$ODIN3D_BASE/work/integration"
export ODIN_WS="$ODIN3D_BASE/work/odin_ws"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-90}" ROS_LOCALHOST_ONLY="${ROS_LOCALHOST_ONLY:-0}"
[[ "$ROS_DOMAIN_ID:$ROS_LOCALHOST_ONLY" == 90:0 ]] ||
  { echo '实机需要 ROS_DOMAIN_ID=90、ROS_LOCALHOST_ONLY=0' >&2; exit 2; }

: "${ODIN_MAP_ROOT:=$HOME/odin3d_maps}"
: "${ODIN_PACKAGE_CACHE:=$ODIN3D_BASE/work/map-packages}"
: "${ODIN_WIFI_IFACE:=wlP1p1s0}"
: "${ODIN_WIFI_IP:=}"
: "${ODIN_ASSETS:=$HOME/odin3d-private}"
: "${ODIN_DRIVER_YAML:=$ODIN_ASSETS/control_command.yaml}"
: "${ODIN_DRIVER_LAUNCH_YAML:=$ODIN_DRIVER_YAML}"
: "${ODIN_WEB_VENDOR:=$ODIN_ASSETS/web/vendor}"
: "${ODIN_CONVERTER:=$ODIN_ASSETS/map_to_ply_arm64_v1.2.0}"
: "${DOG_CONTROL_SOURCE:=$ODIN_ASSETS/dog_control3.py}"
: "${DOG_SDK_LIB_DIR:=$HOME/uniubi_robot_sdk_py/.deps/uniubi_robot_sdk/lib/aarch64_host}"
: "${DOG_IFACE:=eno1}"
: "${DOG_DEVICE_ID:=}"
: "${ODIN_LEGACY_BIN_SHA256:=}" "${ODIN_LEGACY_PCD:=}" "${ODIN_LEGACY_PACKAGE:=}"
: "${ODIN_WEB_PORT:=8080}"
: "${ODIN_WEB_TLS_CERT:=}" "${ODIN_WEB_TLS_KEY:=}" "${ODIN_WEB_TRUSTED_HTTP:=no}"
export ODIN_MAP_ROOT ODIN_PACKAGE_CACHE ODIN_DRIVER_YAML ODIN_DRIVER_LAUNCH_YAML
export ODIN_LEGACY_BIN_SHA256 ODIN_LEGACY_PCD ODIN_LEGACY_PACKAGE
export DOG_CONTROL_SOURCE DOG_SDK_LIB_DIR ODIN_VENDOR_AUTHORIZED DOG_VENDOR_AUTHORIZED
export ODIN_EXTRINSICS_CONFIRMED ODIN_WEB_CAMERA_CONFIRMED
export ODIN_WEB_PORT ODIN_WEB_TLS_CERT ODIN_WEB_TLS_KEY
export DOG_DEVICE_ID DOG_IFACE

require_web_access() {
  [[ "$ODIN_WEB_PORT" =~ ^[1-9][0-9]{3,4}$ ]] &&
    ((ODIN_WEB_PORT >= 1024 && ODIN_WEB_PORT <= 65535 && ODIN_WEB_PORT != 9090)) ||
    { echo 'Web 端口须为 1024–65535，避开 ROSBridge 9090。' >&2; return 1; }
  if [[ -n "$ODIN_WEB_TLS_CERT" || -n "$ODIN_WEB_TLS_KEY" ]]; then
    [[ -s "$ODIN_WEB_TLS_CERT" && -s "$ODIN_WEB_TLS_KEY" ]] ||
      { echo '请配置可用的 TLS 证书及私钥。' >&2; return 1; }
  elif [[ "$ODIN_WEB_TRUSTED_HTTP" != yes ]]; then
    echo '请配置 TLS，或由负责人为隔离可信的实验室网络确认使用 HTTP。' >&2
    return 1
  fi
  /usr/bin/python3 -B -c 'import aiohttp' ||
    { echo '缺少系统软件包 python3-aiohttp。' >&2; return 1; }
}

resolve_web_address() {
  local addresses
  addresses="$(ip -o -4 addr show dev "$ODIN_WIFI_IFACE" scope global |
    awk -v requested="$ODIN_WIFI_IP" '{split($4, a, "/"); if (requested == "" || a[1] == requested) print a[1]}' |
    sort -u)" || return 1
  [[ -n "$addresses" && "$addresses" != *$'\n'* ]] || return 1
  printf '%s\n' "$addresses"
}

require_workspace_boundary() {
  local work="$ODIN3D_BASE/work" path
  [[ "$(realpath -m -- "$work")" == "$work" &&
     "$(realpath -m -- "$ODIN_ROOT")" == "$ODIN_ROOT" &&
     "$(realpath -m -- "$ODIN_WS")" == "$ODIN_WS" ]] ||
    { echo '隔离工作区指向其他位置，停止。' >&2; return 1; }
  for path in "$ODIN3D_BASE" "$ODIN_MAP_ROOT" "$ODIN_PACKAGE_CACHE"; do
    [[ "$path" == /* ]] || { echo '地图及缓存需要绝对路径。' >&2; return 1; }
    case "$(realpath -m -- "$path")/" in
      "$(realpath -m -- "$HOME/odin_nav_integration")/"*|"$(realpath -m -- "$HOME/odin_ws")/"*)
        echo '请将一键包、地图输出及缓存放在生产部署目录之外。' >&2; return 1 ;;
    esac
  done
}

require_isolated_configs() {
  require_workspace_boundary || return 1
  local target
  for target; do
    [[ "$target" == "$ODIN_WS/"* && "$(stat -c %h -- "$target")" == 1 ]] ||
      { echo "拒绝替换隔离工作区之外或具有硬链接的 YAML：$target" >&2; return 1; }
  done
}

require_session_owner() {
  local session="$1" owner="$2" current
  current="$(tmux show-environment -t "=$session" ODIN_RUN_OWNER 2>/dev/null)" || current=''
  [[ -n "$owner" && "ODIN_RUN_OWNER=$owner" == "$current" ]] ||
    { echo "会话 $session 与本部署的恢复记录不匹配，请联系负责人。" >&2; return 1; }
}

require_vendor_access() {
  local errors=0
  if [[ "${ODIN_VENDOR_AUTHORIZED:-no}" != yes || "${DOG_VENDOR_AUTHORIZED:-no}" != yes ]]; then
    echo '待处理：负责人需确认 Odin 和宇泛目标设备的合法授权。' >&2
    errors=1
  fi
  if [[ ! -s "$DOG_CONTROL_SOURCE" || ! -s "$DOG_SDK_LIB_DIR/librobotMotionSdk.so" ]] ||
     ! /usr/bin/python3 -c 'from importlib.util import find_spec; raise SystemExit(find_spec("robot_motion_sdk") is None)'; then
    echo '待处理：宇泛控制脚本、ARM64 SDK 库或系统 Python 的 robot_motion_sdk 缺失。' >&2
    errors=1
  fi
  return "$errors"
}
require_workspace_boundary || exit 2
