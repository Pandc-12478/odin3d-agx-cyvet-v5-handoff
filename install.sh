#!/usr/bin/env bash
set -Eeuo pipefail
base="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$base/config.env" ]]; then
  umask 077
  cp -- "$base/config.env.example" "$base/config.env"
  chmod 600 -- "$base/config.env"
  echo "已生成 $base/config.env。请由负责人填写授权确认、资源路径、无线网卡和相机条件，然后重运行 bash install.sh。"
  exit 2
fi
source "$(dirname -- "${BASH_SOURCE[0]}")/odin3d-settings.sh"
umask 077

project_rev=9b642ad9a5677c491bae8fc08694a21989cbc100
driver_rev=f51051f2d861f7643d4d33d2ade2952efe1a4672
driver="$ODIN_WS/src/odin_ros_driver"
nav_ws="$ODIN_ROOT/nav_ws"
vendor="$nav_ws/src/jie_3d_nav-main/jie_octomap/web/vendor"
patch_file="$ODIN3D_BASE/integration.patch"
vendor_files=(three.module.js roslib.min.js jsm/controls/OrbitControls.js)
ready="$ODIN3D_BASE/work/install.complete"
phase='初始化'
trap 'printf "[失败] %s（第 %s 行）。处理后重选安装，构建日志和原件均保留。\n" "$phase" "$LINENO" >&2' ERR
fail() { printf '[失败] %s：%s\n' "$phase" "$*" >&2; exit 2; }
need() { [[ -s "$1" ]] || fail "缺少 $2：$1"; }
has_driver_library() {
  local lib
  for lib in "$driver"/lib/liblydHostApi_arm.*; do
    [[ -f "$lib" && -s "$lib" ]] && return 0
  done
  return 1
}

check() {
  local f
  printf '源码：项目 %s；Odin %s\n' \
    "$(git -C "$ODIN_ROOT" rev-parse --short HEAD 2>/dev/null || echo 待获取)" \
    "$(git -C "$driver" rev-parse --short HEAD 2>/dev/null || echo 待获取)"
  for f in git colcon rosdep tmux flock ss ip curl pgrep; do
    command -v "$f" >/dev/null || echo "待处理：缺少 $f"
  done
  for f in /opt/ros/humble/setup.bash "$ODIN_WS/install/setup.bash" "$nav_ws/install/setup.bash"; do
    [[ -s "$f" ]] || echo "待处理：$f"
  done
  [[ -f "$ready" ]] || echo '待处理：向导尚未完成两次编译。'
  require_vendor_access || true
  require_web_access || true
  has_driver_library || echo '待处理：隔离驱动树缺少 Odin ARM64 厂商库。'
  [[ -s "$ODIN_DRIVER_YAML" && -s "$ODIN_DRIVER_LAUNCH_YAML" ]] ||
    echo '待处理：缺少厂商适配的驱动 YAML。'
  [[ -x "$ODIN_CONVERTER" ]] || echo '待处理：缺少可执行的 ARM64 BIN 转换器。'
  for f in "${vendor_files[@]}"; do
    [[ -s "$ODIN_WEB_VENDOR/$f" ]] || echo "待处理：Web 资源 $f"
  done
  /usr/bin/python3 -c 'from importlib.util import find_spec; [print("待处理：Python " + p) for p in ("yaml","numpy","open3d") if find_spec(p) is None]'
  resolve_web_address >/dev/null || echo '待处理：无线接口地址缺失或存在多个候选。'
  [[ "${ODIN_EXTRINSICS_CONFIRMED:-no}" == yes ]] || echo '待处理：负责人确认外参。'
  return 0
}

checkout() {
  local repo="$1" url="$2" rev="$3" current
  [[ "$(realpath -m -- "$repo")" == "$repo" && ! -L "$repo/.git" ]] ||
    fail "源码目录指向隔离区外：$repo"
  if [[ ! -d "$repo/.git" ]]; then
    [[ ! -e "$repo" || ( -d "$repo" && -z "$(ls -A -- "$repo")" ) ]] ||
      fail "已有未纳入向导的源码目录：$repo"
    git init -q "$repo"
  fi
  [[ "$(git -C "$repo" rev-parse --show-toplevel)" == "$repo" ]] || fail "仓库根目录不符：$repo"
  current="$(git -C "$repo" rev-parse --verify HEAD 2>/dev/null || true)"
  if [[ -n "$current" ]]; then
    [[ "$current" == "$rev" ]] || fail "已有源码版本不同：$repo（$current）"
    return
  fi
  if ! git -C "$repo" cat-file -e "$rev^{commit}" 2>/dev/null; then
    git -C "$repo" fetch --no-tags "$url" "$rev"
  fi
  git -C "$repo" checkout -q --detach "$rev"
}

apply_once() {
  local repo="$1" patch="$2"
  if git -C "$repo" apply --reverse --check "$patch" >/dev/null 2>&1; then return; fi
  git -C "$repo" apply --check "$patch" || fail "源码与核对过的补丁不匹配：$repo"
  git -C "$repo" apply "$patch"
}

copy_resource() {
  local source="$1" dest="$2" stage
  [[ "$(realpath -m -- "$dest")" == "$dest" ]] || fail "资源目标指向其他位置：$dest"
  if [[ -e "$dest" ]] && cmp -s -- "$source" "$dest"; then return; fi
  [[ ! -e "$dest" ]] || fail "已有不同资源：$dest；请负责人核对"
  mkdir -p -- "$(dirname -- "$dest")"
  stage="$(mktemp -- "$dest.XXXXXXXX")"
  cp -- "$source" "$stage"
  mv -nT -- "$stage" "$dest"
  [[ ! -e "$stage" ]] || fail "资源目标已变化：$dest"
}

check_offline_dependencies() {
  # rosdep's GitHub index is often unreachable on the lab network. Check the
  # direct manifest dependencies against locally installed ROS and apt packages.
  /usr/bin/python3 - "$driver" "$nav_ws/src" <<'PYTHON'
import pathlib
import subprocess
import sys
import xml.etree.ElementTree as ET

paths = [pathlib.Path(sys.argv[1]) / "package.xml"]
paths += list(pathlib.Path(sys.argv[2]).rglob("package.xml"))
packages = [ET.parse(path).getroot() for path in paths]
local = {root.findtext("name") for root in packages}
required = {
    node.text.strip()
    for root in packages
    for node in root
    if node.tag in {"depend", "build_depend", "buildtool_depend", "exec_depend"}
    and node.text and node.text.strip() not in local
}

def exists(command):
    return subprocess.run(command, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0

missing = []
for name in sorted(required):
    if name.startswith("python3-"):
        result = subprocess.run(["dpkg-query", "-W", "-f=${Status}", name],
                                capture_output=True, text=True)
        ok = result.returncode == 0 and result.stdout == "install ok installed"
    elif name in {"opencv", "tinyxml2"}:
        ok = exists(["pkg-config", "--exists", {"opencv": "opencv4", "tinyxml2": "tinyxml2"}[name]])
    else:
        ok = exists(["ros2", "pkg", "prefix", name])
    if not ok:
        missing.append(name)
if missing:
    print("[失败] 离线依赖检查缺少：" + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
print("rosdep 索引不可用；已核对本机 ROS/apt 的直接运行与编译依赖。")
PYTHON
}

case "${1:-}" in
  --check) check; exit ;;
  ''|--source) ;;
  *) fail '用法：install.sh [--check | --source]' ;;
esac
[[ -s "$ODIN3D_ENV" ]] || fail '负责人先按 config.env.example 准备私有配置'
[[ "$(uname -m)" == aarch64 ]] || fail '首次安装需要 ARM64 AGX'
for tool in git flock; do command -v "$tool" >/dev/null || fail "缺少 $tool"; done
mkdir -p -- "$ODIN3D_BASE/work"
exec 8>"$ODIN3D_BASE/work/install.lock"
flock -n 8 || fail '已有安装向导在运行'
phase='获取固定版本源码'
checkout "$ODIN_ROOT" https://github.com/Pandc-12478/agx-odin1-quadruped-navigation.git "$project_rev"
checkout "$driver" https://github.com/manifoldsdk/odin_ros_driver.git "$driver_rev"
[[ "${1:-}" != --source ]] || { echo '源码已就绪。请负责人准备授权、厂商资源及外参。'; exit 0; }

phase='检查厂商资源与系统依赖'
exec 7>"$ODIN3D_BASE/work/web-server.lock"
flock -n 7 || fail '请先正常退出本部署的 Web 控制台'
mkdir -p -- "$ODIN_ROOT/onekey_mapping_state"
exec 9>"$ODIN_ROOT/onekey_mapping_state/session.lock"
flock -n 9 || fail '建图或导航正在操作配置'
[[ ! -e "$ODIN_ROOT/onekey_mapping_state/active.env" && ! -L "$ODIN_ROOT/onekey_nav_state/active" ]] ||
  fail '隔离工作区有待恢复的会话'
for tool in colcon rosdep tmux ip ss curl pgrep; do command -v "$tool" >/dev/null || fail "缺少 $tool"; done
need /opt/ros/humble/setup.bash 'ROS2 Humble'
require_vendor_access || fail '授权或宇泛 SDK 待处理'
require_web_access || fail 'Web 监听配置或系统依赖待处理'
has_driver_library || fail "请按厂商文档安装 Odin ARM64 库到 $driver/lib"
need "$ODIN_DRIVER_YAML" '厂商适配的驱动 YAML'
need "$ODIN_DRIVER_LAUNCH_YAML" 'Launch 驱动 YAML'
[[ -x "$ODIN_CONVERTER" ]] || fail "请放置厂商 ARM64 转换器：$ODIN_CONVERTER"
for f in "${vendor_files[@]}"; do need "$ODIN_WEB_VENDOR/$f" 'Web 离线资源'; done
/usr/bin/python3 -c 'import yaml; from importlib.util import find_spec; raise SystemExit(any(find_spec(p) is None for p in ("numpy","open3d")))' ||
  fail '系统 Python 需要 PyYAML、NumPy、Open3D'
/usr/bin/python3 - "$ODIN_DRIVER_YAML" "$ODIN_DRIVER_LAUNCH_YAML" <<'PYTHON' || fail '请检查驱动 YAML 的模式和保存/重定位字段'
import sys
import yaml

for path in sys.argv[1:]:
    with open(path) as stream:
        keys = yaml.safe_load(stream)["register_keys"]
    assert keys["custom_map_mode"] == 2
    assert {"mapping_result_dest_dir", "mapping_result_file_name", "relocalization_map_abs_path"} <= keys.keys()
PYTHON

phase='应用一键化补丁并准备私有文件'
apply_once "$ODIN_ROOT" "$patch_file"
apply_once "$driver" "$ODIN_ROOT/config/odin_ros_driver-package.patch"
copy_resource "$ODIN3D_BASE/compat/run_mapping_preview.sh" \
  "$ODIN_ROOT/odin_onekey_mapping/run_mapping_preview.sh"
chmod u+x -- "$ODIN_ROOT/odin_onekey_mapping/run_mapping_preview.sh"
if [[ ! -L "$ODIN_ROOT/odin3d-settings.sh" ]]; then
  ln -s -- "$ODIN3D_BASE/odin3d-settings.sh" "$ODIN_ROOT/odin3d-settings.sh"
fi
[[ "$(realpath -e -- "$ODIN_ROOT/odin3d-settings.sh")" == "$ODIN3D_BASE/odin3d-settings.sh" ]] ||
  fail '设置入口指向其他部署'
for f in "${vendor_files[@]}"; do copy_resource "$ODIN_WEB_VENDOR/$f" "$vendor/$f"; done
# The fixed upstream snapshot references these five generated entry points and
# a worlds directory in CMake, but does not track them. Use the verified V4
# copies so a fresh checkout builds like the original AGX workspace.
nav_scripts="$nav_ws/src/jie_3d_nav-main/jie_octomap/scripts"
for f in map_package_manager map_save_gui map_viewer_gui ros_map_import_gui pcd_map_import_gui; do
  copy_resource "$ODIN3D_BASE/compat/scripts/$f" "$nav_scripts/$f"
  chmod u+x -- "$nav_scripts/$f"
done
mkdir -p -- "$nav_ws/src/jie_3d_nav-main/jie_octomap/worlds"

# Build the driver with its target-machine configuration. Preserve the upstream
# original via Git and the private replacement outside both repositories.
driver_config="$driver/config/control_command.yaml"
[[ "$(realpath -e -- "$driver_config")" == "$driver_config" &&
   "$(stat -c %h -- "$driver_config")" == 1 ]] || fail '驱动源码 YAML 路径不在隔离区'
if ! cmp -s -- "$ODIN_DRIVER_YAML" "$driver_config"; then
  git -C "$driver" diff --quiet HEAD -- config/control_command.yaml ||
    fail '驱动 YAML 已存在不同现场修改，请负责人核对'
  mkdir -p -m 700 -- "$ODIN3D_BASE/work/backups"
  copy_resource "$driver_config" "$ODIN3D_BASE/work/backups/control_command.upstream.yaml"
  stage="$(mktemp -- "$driver_config.XXXXXXXX")"
  cp -- "$ODIN_DRIVER_YAML" "$stage"
  mv -T -- "$stage" "$driver_config"
fi

phase='核对 ROS 系统依赖'
set +u
source /opt/ros/humble/setup.bash
set -u
if ! rosdep check --from-paths "$driver" "$nav_ws/src" --ignore-src --rosdistro humble; then
  check_offline_dependencies || fail '请根据缺失包清单补齐系统依赖，再重试'
fi

phase='编译 Odin 驱动'
driver_build_args=(--symlink-install)
driver_install_mode=symlink
if ! cmp -s -- "$ODIN_DRIVER_YAML" "$ODIN_DRIVER_LAUNCH_YAML"; then
  driver_build_args=()
  driver_install_mode=copy
fi
mode_file="$ODIN3D_BASE/work/driver.install-mode"
if [[ -f "$mode_file" ]]; then
  [[ "$(cat -- "$mode_file")" == "$driver_install_mode" ]] ||
    fail '两份 YAML 的部署方式已变更，请为该配置建立新的隔离工作区'
else
  stage="$(mktemp -- "$mode_file.XXXXXXXX")"
  printf '%s\n' "$driver_install_mode" > "$stage"
  mv -T -- "$stage" "$mode_file"
fi
if [[ -f "$ready" ]]; then unlink "$ready"; fi
(cd "$ODIN_WS" && colcon build "${driver_build_args[@]}")
phase='核对安装版驱动 YAML'
launch_config="$ODIN_WS/install/odin_ros_driver/share/odin_ros_driver/config/control_command.yaml"
launch_target="$(realpath -e -- "$launch_config")"
require_isolated_configs "$launch_target"
if ! cmp -s -- "$ODIN_DRIVER_LAUNCH_YAML" "$launch_target"; then
  [[ "$launch_target" == "$launch_config" ]] ||
    fail '源和安装 YAML 指向同一文件，无法使用两份现场配置'
  mkdir -p -m 700 -- "$ODIN3D_BASE/work/backups"
  if [[ ! -f "$ODIN3D_BASE/work/backups/control_command.launch.before.yaml" ]]; then
    copy_resource "$launch_target" "$ODIN3D_BASE/work/backups/control_command.launch.before.yaml"
  fi
  stage="$(mktemp -- "$launch_target.XXXXXXXX")"
  cp -- "$ODIN_DRIVER_LAUNCH_YAML" "$stage"
  mv -T -- "$stage" "$launch_target"
fi
set +u
source "$ODIN_WS/install/setup.bash"
set -u
phase='编译导航工作区'
(cd "$nav_ws" && colcon build --symlink-install)
[[ -s "$nav_ws/install/setup.bash" ]] || fail '未生成导航环境入口'
phase='核对 Web 和 ROSBridge 安装'
set +u
source "$nav_ws/install/setup.bash"
set -u
ros2 pkg prefix rosbridge_server >/dev/null || fail '请安装 ros-humble-rosbridge-server'
share="$(ros2 pkg prefix --share jie_octomap)/web"
for f in index.html main.js styles.css "${vendor_files[@]/#/vendor/}"; do
  need "$share/$f" '已安装的三维页面文件'
done
grep -Fq '/planner/socket' "$share/main.js" || fail '安装版三维页面未包含统一控制台接入'
touch -- "$ready"
chmod u+x -- "$ODIN3D_BASE/odin3d"
echo '安装完成。启动命令：./odin3d start'
