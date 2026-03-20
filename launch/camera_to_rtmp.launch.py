from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("camera_index", default_value="0"),
        DeclareLaunchArgument("width", default_value="640"),
        DeclareLaunchArgument("height", default_value="360"),
        DeclareLaunchArgument("fps", default_value="15"),
        DeclareLaunchArgument("image_topic", default_value="/camera/image_raw"),
        DeclareLaunchArgument("rtmp_url", default_value="rtmp://localhost:1935/stream/stream"),

        Node(
            package="video_streaming",
            executable="camera_publisher_node",
            name="camera_publisher_node",
            output="screen",
            arguments=[
                "--camera-index", LaunchConfiguration("camera_index"),
                "--width", LaunchConfiguration("width"),
                "--height", LaunchConfiguration("height"),
                "--fps", LaunchConfiguration("fps"),
                "--output-topic", LaunchConfiguration("image_topic"),
            ],
        ),

        Node(
            package="video_streaming",
            executable="rtmp_bridge_node",
            name="rtmp_bridge_node",
            output="screen",
            arguments=[
                "--input-topic", LaunchConfiguration("image_topic"),
                "--width", LaunchConfiguration("width"),
                "--height", LaunchConfiguration("height"),
                "--fps", LaunchConfiguration("fps"),
                "--rtmp-url", LaunchConfiguration("rtmp_url"),
            ],
        ),
    ])
