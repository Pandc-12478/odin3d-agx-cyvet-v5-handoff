#!/usr/bin/env bash

list_maps() {
  [[ -d "$MAP_ROOT" ]] || fail "地图目录不存在：$MAP_ROOT"
  mapfile -d '' MAPS < <(find "$MAP_ROOT" -type f -name '*.bin' -size +0c -print0 | sort -z)
  (("${#MAPS[@]}" > 0)) || fail "未在 $MAP_ROOT 找到非空 BIN"
  for i in "${!MAPS[@]}"; do
    printf '%3d  %s\n' "$((i + 1))" "${MAPS[i]}"
  done
}

select_map() {
  local requested="${1:-}" choice maps_dir
  if [[ -z "$requested" ]]; then
    list_maps
    [[ -t 0 ]] || fail '请在终端交互选图，或传入 BIN 的绝对路径'
    read -rp '选择地图编号：' choice || fail '未选择地图'
    [[ "$choice" =~ ^[1-9][0-9]{0,7}$ ]] && ((choice <= ${#MAPS[@]})) || fail '地图编号无效'
    requested="${MAPS[choice - 1]}"
  fi
  MAP_BIN="$(realpath -e -- "$requested")" || fail "地图路径不存在：$requested"
  maps_dir="$(realpath -e -- "$MAP_ROOT")"
  [[ "$MAP_BIN" == "$maps_dir/"*.bin && "$MAP_BIN" != *$'\n'* && "$MAP_BIN" != *$'\r'* &&
     -f "$MAP_BIN" && -r "$MAP_BIN" && -s "$MAP_BIN" ]] ||
    fail "请选择 $MAP_ROOT 下可读且非空的 BIN 文件"
}

validate_package() {
  /usr/bin/python3 - "$1" <<'PY'
from pathlib import Path
import re
import sys
import yaml

package = Path(sys.argv[1])
meta = yaml.safe_load((package / 'meta.yaml').read_text())
map_id = meta['map_id']
if (meta['frame_id'] != 'map' or not re.fullmatch(r'[A-Za-z0-9_-]+', map_id)
        or meta['layers']['traversable_count'] <= 0):
    raise SystemExit(f'Invalid or empty traversable map package: {package}')
for key, name in (('octomap_file', 'octomap_msg.npz'), ('layers_file', 'layers.npz')):
    file = package / name
    if meta[key] != name or not file.is_file() or file.stat().st_size == 0:
        raise SystemExit(f'Missing or invalid package file: {file}')
print(map_id)
PY
}

prepare_package() {
  local convert_only="${1:-no}" hash cache stage original offline_nodes converted converted_tmp
  original="$(stat -c '%d:%i:%s:%y:%z' -- "$MAP_BIN")"
  hash="$(sha256sum < "$MAP_BIN")"
  hash="${hash%% *}"
  MAP_SIGNATURE="$original"
  if [[ -n "$ODIN_LEGACY_BIN_SHA256" && "$hash" == "$ODIN_LEGACY_BIN_SHA256" ]]; then
    # The PCD build log records map_20261111_173026.pcd as the package input.
    MAP_PACKAGE="$ODIN_LEGACY_PACKAGE"
    MAP_PCD="$ODIN_LEGACY_PCD"
  else
    cache="$ODIN_PACKAGE_CACHE"
    MAP_PACKAGE="$cache/$hash/package"
    MAP_PCD="$cache/$hash/map.pcd"
    if [[ ! -d "$MAP_PACKAGE" ]]; then
      mkdir -p -- "$cache"
      converted="$cache/converted/$hash.pcd"
      if [[ "$convert_only" == yes && -s "$converted" ]]; then info 'PCD 已生成'; return; fi
      stage="$(mktemp -d -- "$cache/.build.XXXXXXXX")"
      if [[ ! -s "$converted" ]]; then
        info 'BIN → PLY'
        [[ -x "$ODIN_CONVERTER" ]] || fail "BIN → PLY：缺少厂商转换器 $ODIN_CONVERTER"
        "$ODIN_CONVERTER" --format auto --binary "$MAP_BIN" "$stage/map.ply" 2>&1 |
          tee "$stage/export.log" || fail "BIN → PLY 失败：$stage/export.log"
        info 'PLY → PCD'
        /usr/bin/python3 - "$stage/map.ply" "$stage/map.pcd" 2>"$stage/pcd.log" <<'PY' || fail "PLY → PCD 失败：$stage/pcd.log"
import sys
import numpy as np
import open3d as o3d

source, destination = sys.argv[1:]
cloud = o3d.io.read_point_cloud(source)
if cloud.is_empty() or not np.isfinite(np.asarray(cloud.points)).all():
    raise SystemExit('PLY has no points or contains non-finite coordinates')
if not o3d.io.write_point_cloud(destination, cloud):
    raise SystemExit('PCD write failed')
print(f'{len(cloud.points)} points -> {destination}')
PY
        [[ "$(stat -c '%d:%i:%s:%y:%z' -- "$MAP_BIN")" == "$original" ]] ||
          fail 'BIN 在转换期间发生变化'
        mkdir -p -- "$cache/converted"
        converted_tmp="$(mktemp -- "$cache/converted/.pcd.XXXXXXXX")"
        cp -- "$stage/map.pcd" "$converted_tmp"
        mv -nT -- "$converted_tmp" "$converted"
        [[ ! -e "$converted_tmp" ]] || fail '已有同名 PCD，请负责人核对缓存'
      else
        cp -- "$converted" "$stage/map.pcd"
      fi
      if [[ "$convert_only" == yes ]]; then info 'PCD 已生成'; return; fi
      info '正在生成导航地图包'
      offline_nodes="$(ROS_DOMAIN_ID=78 ROS_LOCALHOST_ONLY=1 timeout 8 ros2 node list --no-daemon --spin-time 2)" ||
        fail '无法检查离线构建域 78'
      [[ -z "$offline_nodes" ]] || fail '离线域 78 正在使用，请结束原离线进程后重试'
      ROS_DOMAIN_ID=78 ROS_LOCALHOST_ONLY=1 /usr/bin/python3 "$BRIDGE/build_new_map.py" \
        "$stage/map.pcd" "$stage/package" --min-points 8 --map-id "odin_${hash:0:16}_r020" \
        >"$stage/package.log" 2>&1 || fail "生成导航地图包失败：$stage/package.log"
      validate_package "$stage/package" >/dev/null || fail "生成的地图包需要检查：$stage"
      [[ "$(stat -c '%d:%i:%s:%y:%z' -- "$MAP_BIN")" == "$original" ]] ||
        fail 'BIN 在转换期间发生变化'
      printf '%s  %s\n' "$hash" "$MAP_BIN" > "$stage/source_bin.sha256"
      mv -nT -- "$stage" "$cache/$hash"
      [[ ! -d "$stage" ]] || fail "同一地图包已由其他进程创建：$MAP_PACKAGE"
    fi
  fi
  [[ -s "$MAP_PCD" ]] || fail "找不到对应的 PCD：$MAP_PCD"
  if [[ "$convert_only" == yes ]]; then info 'PCD 已生成'; return; fi
  MAP_ID="$(validate_package "$MAP_PACKAGE")" || fail "无法使用导航包：$MAP_PACKAGE"
  [[ "$(stat -c '%d:%i:%s:%y:%z' -- "$MAP_BIN")" == "$original" ]] ||
    fail 'BIN 在校验或准备期间发生变化'
  info "规划地图包：$MAP_PACKAGE"
}
