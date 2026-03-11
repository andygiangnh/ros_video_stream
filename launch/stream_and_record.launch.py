from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("host",         default_value="0.0.0.0"),
        DeclareLaunchArgument("port",         default_value="8080"),
        DeclareLaunchArgument("camera_index", default_value="0"),
        DeclareLaunchArgument("width",        default_value="640"),
        DeclareLaunchArgument("height",       default_value="360"),
        DeclareLaunchArgument("fps",          default_value="15"),
        DeclareLaunchArgument("output_dir",   default_value="/recordings"),
        DeclareLaunchArgument("input_topic",  default_value="/camera/image_raw"),

        Node(
            package="video_streaming",
            executable="webrtc_camera_node",
            name="webrtc_camera_node",
            output="screen",
            arguments=[
                "--host",         LaunchConfiguration("host"),
                "--port",         LaunchConfiguration("port"),
                "--camera-index", LaunchConfiguration("camera_index"),
                "--width",        LaunchConfiguration("width"),
                "--height",       LaunchConfiguration("height"),
                "--fps",          LaunchConfiguration("fps"),
            ],
        ),

        Node(
            package="video_streaming",
            executable="video_recorder_node",
            name="video_recorder_node",
            output="screen",
            arguments=[
                "--output-dir",   LaunchConfiguration("output_dir"),
                "--width",        LaunchConfiguration("width"),
                "--height",       LaunchConfiguration("height"),
                "--fps",          LaunchConfiguration("fps"),
                "--input-topic",  LaunchConfiguration("input_topic"),
            ],
        ),
    ])
