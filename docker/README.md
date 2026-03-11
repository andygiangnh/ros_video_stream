# ROS2 + OpenCV + WebRTC Container

This folder provides a Dockerized setup to run a ROS 2 node that streams a local camera to a browser via WebRTC.

## Prerequisites

- Docker installed and running
- Linux camera device available (default: `/dev/video0`)

## 1) Build image

From repository root:

```bash
chmod +x docker/build_image.sh docker/run_container.sh docker/entrypoint.sh
./docker/build_image.sh
```

## 2) Run container and start stream node

```bash
./docker/run_container.sh
```

Optional environment variables:

- `CAMERA_DEVICE` (default `/dev/video0`)
- `HOST_PORT` (default `8080`)
- `USE_HOST_NETWORK` (default `1`)
- `HOST_IP` to override auto-detected LAN IP shown in startup logs
- `CAMERA_INDEX` (default `0`)
- `WIDTH` (default `640`)
- `HEIGHT` (default `360`)
- `FPS` (default `15`)

Example:

```bash
CAMERA_DEVICE=/dev/video2 HOST_PORT=8090 ./docker/run_container.sh
```

Low-latency example:

```bash
WIDTH=640 HEIGHT=360 FPS=12 ./docker/run_container.sh
```

## Mobile browser access

1. Ensure phone and PC are on the same Wi-Fi/LAN.
2. Start container with host networking (default):

```bash
USE_HOST_NETWORK=1 ./docker/run_container.sh
```

3. Open mobile browser using your PC LAN IP (not `localhost`), e.g.:

```text
http://192.168.x.x:8080
```

4. If page opens but stream does not, allow firewall inbound on TCP 8080.

## 3) Open browser

Open:

- `http://localhost:8080` (or your chosen `HOST_PORT`)

Click **Start** to create a WebRTC session and view the live camera stream.

## 4) Manual command (without script)

```bash
docker run --rm -it \
  --device /dev/video0:/dev/video0 \
  --network host \
  video_streaming:humble \
  ros2 run video_streaming webrtc_camera_node --host 0.0.0.0 --port 8080 --camera-index 0
```
