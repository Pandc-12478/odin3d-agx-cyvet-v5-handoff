# Odin3D V5 开发交接

AGX Orin + Odin1 + 宇泛灵猫三维建图与导航。这里保存 2026-11-13 冻结 Demo 的项目 Web 与集成源码。

## 先看哪里

1. [DEPLOY.md](DEPLOY.md) 了解安装入口和外部依赖。
2. [web/](web/) 查看统一控制台。
3. [建图脚本](work/integration/odin_onekey_mapping/) 查看扫描和保存。
4. [导航脚本](work/integration/odin_onekey_nav/) 查看地图转换、重定位和启动。
5. [桥接代码](work/integration/bridge/) 查看外参、控制器和 SDK 接入。

运行入口为 `./odin3d`，安装入口为 `bash install.sh`。源码依据 2026-11-13 20:22 的冻结快照，接手时核对 AGX 现场版本。

## 配套源码

原始 `jie_3d_nav` 导航包及早期集成源码在[原有私有仓库](https://github.com/Pandc-12478/agx-odin1-quadruped-navigation)。冻结 Demo 对其修改记录在 `integration.patch` 与 `GIT/integration.diff`。Odin ROS 驱动上游基线为 `f51051f2d861f7643d4d33d2ade2952efe1a4672`，修改记录在 `GIT/odin_driver.diff`。

厂商 ARM64 SDK、BIN 转换工具、`dog_control3.py`、地图、设备 ID、密钥和 AGX 本机配置由设备负责人另行交接。这里的配置文件是示例。更换设备时重新核对外参。三方源码及授权说明见原仓库的 `THIRD_PARTY.md`。

冻结 Demo 已有建图、保存、同源地图处理、重定位及网页选点规划证据。真实 SDK 接管、跟踪到点和停止链仍需现场验收。
