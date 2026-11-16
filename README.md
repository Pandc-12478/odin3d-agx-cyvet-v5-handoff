# Odin3D V5 冻结源码快照

这里保留 AGX Orin、Odin1 和宇泛灵猫项目于 2026-11-13 冻结的 V5 源码，供对照现场部署和查阅历史实现。

**正式交接从[私有项目仓库](https://github.com/Pandc-12478/agx-odin1-quadruped-navigation)开始。** 接手人获得访问权限后，可以在首页找到 SSH 开发版和 V5 Web 演示版。V5 的首次部署按[正式部署说明](https://github.com/Pandc-12478/agx-odin1-quadruped-navigation/blob/main/v5-web/DEPLOY.md)操作。

**本快照的安装限制。** 默认布局下，[冻结版安装器](install.sh)会将快照自带的 `work/` 源码目录判定为已有目录。需要重新检出时，安装器仍依赖私有仓库中的固定基线。新克隆本快照后，请使用正式交接仓库的部署入口。

## 代码从哪里读

- `web/` 负责统一控制台，处理建图、地图、规划和运动按钮。
- `work/integration/odin_onekey_mapping/` 负责扫描、保存 BIN 和恢复驱动配置。
- `work/integration/odin_onekey_nav/` 负责转换地图、加载、重定位和启动导航。
- `work/integration/bridge/` 负责相机到机体的外参、控制器和宇泛 SDK 桥接。
- `work/integration/nav_ws/src/jie_3d_nav-main/` 保存冻结版导航 ROS 包。
- `work/odin_ws/src/odin_ros_driver/` 保存冻结版 Odin ROS 驱动源码。
- `private-adapter/dog_control3.py` 保存机器狗的连接和接管代码，默认设备 ID 已改为读取本机环境变量。

[快照依赖清单](DEPLOY.md)列出现场要交接的资源，[排障说明](TROUBLESHOOTING.md)适用于已部署环境。冻结时的源码修改记录在 `GIT/` 和 `integration.patch`，上游项目与离线库见[第三方来源](THIRD_PARTY.md)。

## 当前还需补齐

宇泛 ARM64 SDK、Odin1 厂商二进制库和 BIN 转换工具已从原 AGX 收集并校验，但因这个仓库公开且厂商再分发许可未确认，文件没有上传。具体用途、目标路径和哈希见 [DEPLOY.md](DEPLOY.md)。地图 BIN 超过 GitHub 普通文件大小限制，现场地图不属于公开源码。网页所需的离线 Three.js/ROSLib 文件已放在导航包的 `web/vendor/`。设备 ID、密钥和现场网络配置只应在接手设备本地配置。

仓库的 `work/odin_ws/src/odin_ros_driver/config/control_command.yaml` 已清除旧机器的地图绝对路径。原设备的相机标定参数保留，序列号注释已移除。更换设备须核对标定与外参。

冻结 Demo 已有建图、保存、同源地图处理、重定位和网页选点规划证据。真实 SDK 接管、短路线到点、停车和掉线锁定，以及全新 AGX 首装仍需现场验收。详见[现场验收记录](VALIDATION.md)。
