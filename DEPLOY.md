# Odin3D V5 快照的依赖与现场交接

部署请从[私有交接仓库的 V5 管理员文档](https://github.com/Pandc-12478/agx-odin1-quadruped-navigation/blob/main/v5-web/DEPLOY.md)开始。本页用于核对冻结快照所需的文件、现场接管和验收边界。

## 这次收到的三个依赖文件（2026-11-14）

以下文件已从原 AGX 复制并按 SHA-256 核对；它们属于厂商依赖，尚未上传到公开仓库。接手人需从有权提供这些文件的项目负责人处取得原件。这里的哈希只用于核对版本，不能代替厂商授权。

| 文件 | 用途及部署位置 | SHA-256 |
| --- | --- | --- |
| `liblydHostApi_arm.a` | Odin1 驱动链接库，放入 `work/odin_ws/src/odin_ros_driver/lib/` | `bee75293de255403707cc198a7fb93ee7d2159a8e5368644eec22ec0c1cd8b56` |
| `map_to_ply_arm64_v1.2.0` | AGX 上运行的 ARM64 BIN 转换程序；原路径 `/home/shjshz/odin3d-v5/private/map_to_ply_arm64_v1.2.0`，部署时由 `ODIN_CONVERTER` 指向可执行文件 | `40fbec29fce350071e4450190d23fe4ce9d1e7459e615eaa0a2cd0d23976e7fa` |
| `robot_motion_sdk/__init__.py` | 宇泛 Python 包的接口文件，原路径 `/home/shjshz/.local/lib/python3.10/site-packages/robot_motion_sdk/__init__.py` | `744dea6af792dc263b27eca5b7722e9218b882edca919bd8bf89760faeb2fff3` |

宇泛 `__init__.py` 单独不能运行，还依赖同一包内的 `_uniubi_robot_motion_py_native*.so`、`media_frame.py` 和 ARM64 SDK 的 `librobotMotionSdk.so` 等库；原 AGX 的较早依赖清单记录了这些文件。转换程序复制到 AGX 后需要执行权限（`chmod +x`）。这些文件仅在 ARM64 AGX 环境检查，未在本工作区运行。

**快照安装限制。** 按默认目录布局，`install.sh` 的检出步骤要求目标目录具有独立 Git 元数据或为空。本仓库已跟踪 `work/integration/` 和 `work/odin_ws/src/odin_ros_driver/` 内的展开源码，新克隆会被该步骤拒绝。安装器的集成基线还需要读取私有交接仓库。请按正式部署说明准备独立目录。

## 本项目自带

交付目录自带五个中文页面、受控 Web API、`odin3d` 与 `install.sh`、建图和地图处理兼容脚本、`integration.patch`、原 `jie_3d_nav` 三维页面接入方式，以及零速准备、独立开始、受控停止与心跳锁定流程。正式交接仓库的安装器在隔离 `work/` 中检出固定版本、应用补丁并编译。地图、厂商 SDK 库、设备 ID、授权文件和私有 `config.env` 不在源码包中。原 V4 与生产部署不参与安装。

## 现场交接时逐项核对

| 资产 | 负责人交接时登记 |
| --- | --- |
| Odin1 厂商库和转换器 | 授权获取人、版本、校验值、AGX 目标目录 |
| 宇泛 SDK 和适配 | 完整 Python 包及原生模块、对应 ARM64 动态库、实机适配文件、授权获取人和部署位置 |
| 复现数据 | 一套经确认可移交的同源 BIN、PCD 和导航包，记录 BIN 的 SHA-256、地图包 ID 和适用场地 |
| 现场配置 | 私有 `config.env`、驱动读取的 YAML、网络与 TLS 配置、接线和安装外参资料的保管人及交接位置 |

接手人用自己的账号确认可读取私有仓库并登录 AGX。将具体文件位置、授权和接手情况登记在内部交接记录。

## 更换设备前核对

- **更换 AGX：**核对 Ubuntu 22.04、ROS 2 Humble、Git、colcon、rosdep、tmux、curl、iproute2、`python3-aiohttp`、`ros-humble-rosbridge-server`，无线网卡与 IP、ROS_DOMAIN_ID=90、ROS_LOCALHOST_ONLY=0、隔离工作区路径、地图目录、Web 监听地址和 TLS。复用已合法取得且与 ARM64 环境匹配的 Odin1 驱动、转换器和 SDK 库；缺项由负责人提供。
- **更换机器狗：**在 AGX 本机权限 600 的 `config.env` 中核对 `DOG_DEVICE_ID`、网卡、SDK 接口、`dog_sdk_bridge`、`real_controller` 与原运动参数。更换品牌或型号可能需要代码适配；不得假定所有机器狗可直接复用。设备 ID 和授权信息不得写入网页、公开仓库或交接 ZIP。
- **更换 Odin1 安装位置或地图：**重新核对外参与现场安装关系；确认 BIN、PCD 与规划包来自同一次建图，不能混用。原算法、外参和限速不由网页自动修改。

## 安装与维护

已有部署可在对应目录使用 `./odin3d status` 检查状态、`./odin3d doctor` 诊断、`./odin3d stop` 停止服务。启动时以输出的实际网址为准。负责人在指定实验室网络管理 Web 访问，按正式部署说明配置监听地址和 TLS。

导航加载后，原 `real_controller` 只待命接收规划路径，不启动 SDK。点击“准备运动”时，浏览器只提交一次现场确认；服务端检查地图、定位、授权资源和私有设备 ID，调用原 `arm_nav.sh --web-approved` 执行零速接管并核对 SDK 反馈，不模拟终端 `READY`。选目标的 WebSocket 代理只允许规划话题。点击“开始导航”后，后端才通过固定链路向原控制器与 SDK Bridge 发送启动请求，并等待实际反馈。页面心跳、服务端和独立租约守护进程在失联、定位丢失或控制节点异常时发起受控停止并保持锁定。停止未确认时必须现场使用物理急停；进程退出不能证明机器狗已安全停止。

## 验收与切换

先在独立副本完成 Shell、Python、前端语法及无设备状态流转检查。现场无运动检查：五个页面、建图点云、保存 BIN、同图转换与规划包、地图加载、持续重定位、三维显示和选点规划；确认选点未触发 SDK 接管、未准备不能开始。随后由现场负责人单独批准并执行零速接管、短距离运动、停止反馈、断线锁定测试；保持物理急停有人值守。本源码交付阶段未执行真实 SDK 接管、站立或行走。

切换前先核对旧 V5 会话及机器狗状态。备份当前 V5 私有配置与日志，按正式部署说明将 V5 放入**新的**隔离目录，使用独立端口验证；确认后再由负责人决定何时停旧 Web 并切换地址。回滚时现场确认设备静止、停止新目录自有服务，重新使用旧 V5 地址。保留地图、日志与旧目录，不在生产部署反向应用补丁。
