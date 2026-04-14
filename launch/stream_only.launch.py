from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("camera_index", default_value="0"),
        DeclareLaunchArgument("host",         default_value="0.0.0.0"),
        DeclareLaunchArgument("port",         default_value="8080"),
        DeclareLaunchArgument("image_topic",  default_value="/camera/image_raw"),
        DeclareLaunchArgument("width",        default_value="640"),
        DeclareLaunchArgument("height",       default_value="360"),
        DeclareLaunchArgument("fps",          default_value="15"),

        Node(
            package="video_streaming",
            executable="camera_publisher_node",
            name="camera_publisher_node",
            output="screen",
            arguments=[
                "--camera-index", LaunchConfiguration("camera_index"),
                "--width",        LaunchConfiguration("width"),
                "--height",       LaunchConfiguration("height"),
                "--fps",          LaunchConfiguration("fps"),
                "--output-topic", LaunchConfiguration("image_topic"),
            ],
        ),

        Node(
            package="video_streaming",
            executable="webrtc_camera_node",
            name="webrtc_camera_node",
            output="screen",
            arguments=[
                "--host",        LaunchConfiguration("host"),
                "--port",        LaunchConfiguration("port"),
                "--image-topic", LaunchConfiguration("image_topic"),
                "--width",       LaunchConfiguration("width"),
                "--height",      LaunchConfiguration("height"),
                "--fps",         LaunchConfiguration("fps"),
            ],
        ),
    ])
