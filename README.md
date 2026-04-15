# video_streaming (KVS)

ROS 2 camera streaming pipeline using AWS Kinesis Video Streams and a separate single-page app hosted on S3.

## Architecture

```mermaid
flowchart LR
    Cam[Local camera /dev/videoX] --> Pub[camera_publisher_node]
    Pub --> Topic[/camera/image_raw]
    Topic --> Bridge[kvs_bridge_node]
    Bridge --> KVS[AWS Kinesis Video Stream]
    Browser[SPA in S3/CloudFront] --> KVS
```

## Repository layout

- `video_streaming/camera_publisher_node.py`: ROS2 camera capture publisher
- `video_streaming/kvs_bridge_node.py`: ROS2 topic to KVS publisher bridge
- `launch/kvs_stream.launch.py`: Launches the KVS ROS2 pipeline
- `spa/`: Static web frontend to be deployed to S3
- `cdk/`: AWS CDK stacks for KVS, IAM, and S3/CloudFront

## Quick start

1. Provision AWS resources with CDK (see `cdk/` and deployment guide).
2. Deploy SPA from `spa/` to S3.
3. Build and run ROS2 nodes:

```bash
colcon build --packages-select video_streaming
source install/setup.bash
ros2 launch video_streaming kvs_stream.launch.py
```

For full deployment and verification instructions, see `DEPLOYMENT_GUIDE.md`.
