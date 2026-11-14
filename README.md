# Odin3D V5 开发交接

这是 AGX Orin、Odin1 和宇泛灵猫项目在 2026-11-13 的冻结源码。入口为 `odin3d` 和 `install.sh`。

## 代码从哪里读

- `web/` 负责统一控制台，处理建图、地图、规划和运动按钮。
- `work/integration/odin_onekey_mapping/` 负责扫描、保存 BIN 和恢复驱动配置。
- `work/integration/odin_onekey_nav/` 负责转换地图、加载、重定位和启动导航。
- `work/integration/bridge/` 负责相机到机体的外参、控制器和宇泛 SDK 桥接。
- `work/integration/nav_ws/src/jie_3d_nav-main/` 保存冻结版导航 ROS 包。
- `work/odin_ws/src/odin_ros_driver/` 保存冻结版 Odin ROS 驱动源码。

[DEPLOY.md](DEPLOY.md) 说明安装和外部依赖。冻结时的源码修改记录在 `GIT/` 和 `integration.patch`。

## 当前还需补齐

宇泛 ARM64 SDK、`dog_control3.py`、Odin1 厂商二进制库和 BIN 转换工具尚未放进仓库。地图 BIN 超过 GitHub 普通文件大小限制。网页的离线 Three.js/ROSLib 资源也尚未放入仓库。设备 ID、密钥和现场网络配置只应在接手设备本地配置。

仓库的 `work/odin_ws/src/odin_ros_driver/config/control_command.yaml` 已清除旧机器的地图绝对路径。原设备的相机标定参数保留，序列号注释已移除。更换设备须核对标定与外参。

冻结 Demo 已有建图、保存、同源地图处理、重定位和网页选点规划证据。真实 SDK 接管、跟踪到点及停止链仍需现场验收。
