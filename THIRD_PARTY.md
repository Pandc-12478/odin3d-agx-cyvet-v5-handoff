# 第三方源码与离线资源

| 项目 | 本快照的来源与标识 |
| --- | --- |
| Odin ROS 驱动 | [manifoldsdk/odin_ros_driver 固定基线](https://github.com/manifoldsdk/odin_ros_driver/tree/f51051f2d861f7643d4d33d2ade2952efe1a4672)。快照保留其 [Apache-2.0 许可文件](work/odin_ws/src/odin_ros_driver/LICENSE)。 |
| jie_3d_nav | [6-robot/jie_3d_nav](https://github.com/6-robot/jie_3d_nav)。快照内的 ROS 包清单声明 MIT，原始上游提交待确认。 |
| Three.js 和 OrbitControls | [mrdoob/three.js](https://github.com/mrdoob/three.js)。`three.module.js` 的 Git blob 为 `8c431f672f563341a36539c163edbfaa6bfa0b6f`，`OrbitControls.js` 为 `89d3ca5f8df497086b61700718497909b8d97f8a`。具体上游发布版本及配套许可文本待核对。 |
| ROSLib | [RobotWebTools/roslibjs](https://github.com/RobotWebTools/roslibjs)。`roslib.min.js` 内嵌的版本号为 `1.4.1`，Git blob 为 `e61cdd8ef4a96e39aff110f9dfa97dcd9192eea2`。已与上游 [1.4.1 发布文件](https://github.com/RobotWebTools/roslibjs/blob/1.4.1/build/roslib.min.js)核对一致，并附上[该版本的 BSD 许可](work/integration/nav_ws/src/jie_3d_nav-main/jie_octomap/web/vendor/ROSLIB-LICENSE.txt)。 |
| Odin1 与宇泛厂商二进制资源 | 由现场负责人按授权提供。版本核对与目标位置见[快照依赖清单](DEPLOY.md)。 |

离线网页资源位于[`jie_octomap/web/vendor/`](work/integration/nav_ws/src/jie_3d_nav-main/jie_octomap/web/vendor/)。
