# video_streaming

## Introduction
`video_streaming` is a ROS 2 package for low-latency camera streaming to web browsers using WebRTC, with optional ROS-based video recording.

It supports two deployment modes:
- **Single-node mode**: ROS node serves WebRTC directly (current/default flow)
- **DDS-isolated mode**: ROS side publishes to RTMP, non-ROS side serves WebRTC

What it does:
- Captures frames from a local camera (default index `0`)
- Serves a WebRTC web app on port `8080`
- Supports multiple browser clients
- Optionally records frames via `video_recorder_node`

## Quick-Start

### A) Run natively (ROS 2 workspace)
From your ROS 2 workspace root (`/home/giangnh101/ros_ws`):

```bash
colcon build --packages-select video_streaming
source install/setup.bash
ros2 launch video_streaming stream_and_record.launch.py
```

Open in browser:
- `http://localhost:8080` (same machine)
- `http://<your-lan-ip>:8080` (phone/another device on same network)

### B) Run with Docker
From `/home/giangnh101/ros_ws/src/video_streaming`:

```bash
chmod +x docker/build_image.sh docker/run_container.sh docker/entrypoint.sh
./docker/build_image.sh
./docker/run_container.sh
```

Open in browser:
- `http://localhost:8080`
- `http://<your-lan-ip>:8080`

Useful Docker overrides:

```bash
CAMERA_DEVICE=/dev/video2 HOST_PORT=8090 WIDTH=640 HEIGHT=360 FPS=15 ./docker/run_container.sh
```

### C) DDS-isolated mode (ROS side + non-ROS WebRTC side)
This mode is for environments where browser-serving PC/container cannot join ROS 2 DDS.

Pipeline:
- ROS container: `camera_publisher_node` → `/camera/image_raw`
- ROS container: `rtmp_bridge_node` publishes to RTMP server
- Non-ROS container: WebRTC gateway reads RTMP and serves browser clients

From `/home/giangnh101/ros_ws/src/video_streaming/docker`:

```bash
docker compose -f docker-compose.bridge.yml up --build
```

Open in browser:
- `http://localhost:8080`
- `http://<your-lan-ip>:8080`

Optional overrides:

```bash
HOST_PORT=8090 CAMERA_DEVICE=/dev/video2 WIDTH=640 HEIGHT=360 FPS=15 docker compose -f docker-compose.bridge.yml up --build
```

## Prerequisites
- ROS 2 Humble (for native run)
- Linux camera device (default `/dev/video0`)
- Docker (for container run)
- Browser with WebRTC support

## Configuration
Default launch/runtime values:
- `host=0.0.0.0`
- `port=8080`
- `camera_index=0`
- `width=640`
- `height=360`
- `fps=15`

DDS-isolated mode defaults:
- `rtmp_url=rtmp://rtmp-server:1935/stream/stream`
- WebRTC gateway port `8080`

Native launch override example:

```bash
ros2 launch video_streaming stream_and_record.launch.py width:=1280 height:=720 fps:=20 camera_index:=0
```

## Troubleshooting
- **Camera not found / cannot open camera**
	- Check device exists: `ls -l /dev/video*`
	- Use correct camera index or device (`camera_index:=...` or `CAMERA_DEVICE=/dev/videoX`)
	- Ensure no other app is locking the camera

- **Browser page opens but no stream**
	- Click **Start** on the web page to begin WebRTC negotiation
	- Check node logs for camera errors
	- Try a lower load: `width:=640 height:=360 fps:=12`

- **Cannot access from phone / another PC**
	- Use `http://<your-lan-ip>:8080` (not `localhost`)
	- Ensure both devices are on the same network
	- Allow inbound TCP port `8080` in firewall

- **Docker container starts but browser cannot connect**
	- Prefer host networking: `USE_HOST_NETWORK=1 ./docker/run_container.sh`
	- If not using host networking, ensure port mapping is set (`HOST_PORT`)

- **DDS-isolated mode: browser loads but no video**
	- Check RTMP bridge logs in `ros-camera-rtmp` service
	- Confirm RTMP URL matches in both services (`rtmp://rtmp-server:1935/stream/stream`)
	- Wait a few seconds after startup for RTMP source to become ready
