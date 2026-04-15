from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    """Generate launch description for KVS streaming pipeline."""
    return LaunchDescription([
        # Camera publisher arguments
        DeclareLaunchArgument("camera_index", default_value="0"),
        DeclareLaunchArgument("output_topic", default_value="/camera/image_raw"),
        DeclareLaunchArgument("width", default_value="640"),
        DeclareLaunchArgument("height", default_value="360"),
        DeclareLaunchArgument("fps", default_value="15"),

        # KVS bridge arguments
        DeclareLaunchArgument("stream_name", default_value="ros2-camera-stream"),
        DeclareLaunchArgument("aws_region", default_value="us-east-1"),

        # Camera publisher node
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
                "--output-topic", LaunchConfiguration("output_topic"),
            ],
        ),

        # KVS bridge node
        Node(
            package="video_streaming",
            executable="kvs_bridge_node",
            name="kvs_bridge_node",
            output="screen",
            arguments=[
                "--image-topic", LaunchConfiguration("output_topic"),
                "--stream-name", LaunchConfiguration("stream_name"),
                "--aws-region", LaunchConfiguration("aws_region"),
                "--width", LaunchConfiguration("width"),
                "--height", LaunchConfiguration("height"),
                "--fps", LaunchConfiguration("fps"),
            ],
        ),
    ])
