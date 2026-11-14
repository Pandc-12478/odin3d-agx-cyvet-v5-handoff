from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    web_root = str(Path(__file__).resolve().parents[1] / "web")
    return LaunchDescription([
        DeclareLaunchArgument("http_port", default_value="8080"),
        DeclareLaunchArgument("ws_port", default_value="9090"),
        DeclareLaunchArgument("launch_rosbridge", default_value="true",
                              description="Set false to reuse an existing rosbridge."),
        ExecuteProcess(
            cmd=["python3", "-m", "http.server", LaunchConfiguration("http_port"),
                 "--bind", "127.0.0.1", "--directory", web_root],
            output="screen",
        ),
        Node(
            package="rosbridge_server",
            executable="rosbridge_websocket",
            name="odin_preview_rosbridge",
            condition=IfCondition(LaunchConfiguration("launch_rosbridge")),
            parameters=[{
                "address": "127.0.0.1",
                "port": ParameterValue(LaunchConfiguration("ws_port"), value_type=int),
            }],
            output="screen",
        ),
    ])
