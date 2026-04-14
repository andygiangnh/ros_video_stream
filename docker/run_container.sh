#!/usr/bin/env bash
set -e

IMAGE_NAME="video_streaming:humble"
CONTAINER_NAME="video_streaming_webrtc"
CAMERA_DEVICE="${CAMERA_DEVICE:-/dev/video0}"
HOST_PORT="${HOST_PORT:-8080}"
USE_HOST_NETWORK="${USE_HOST_NETWORK:-1}"
HOST_IP="${HOST_IP:-}"
CAMERA_INDEX="${CAMERA_INDEX:-0}"
IMAGE_TOPIC="${IMAGE_TOPIC:-/camera/image_raw}"
WIDTH="${WIDTH:-640}"
HEIGHT="${HEIGHT:-360}"
FPS="${FPS:-15}"

ARGS=(
  --rm -it
  --name "${CONTAINER_NAME}"
  --device "${CAMERA_DEVICE}:${CAMERA_DEVICE}"
)

if [[ "${USE_HOST_NETWORK}" == "1" ]]; then
  ARGS+=(--network host)
else
  ARGS+=(-p "${HOST_PORT}:8080")
fi

if [[ -z "${HOST_IP}" ]]; then
  HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
fi

echo "Starting video streaming stack..."
echo "  Open on PC     : http://localhost:${HOST_PORT}"
if [[ -n "${HOST_IP}" ]]; then
  echo "  Open on mobile : http://${HOST_IP}:${HOST_PORT}"
fi
echo "  Camera         : ${CAMERA_DEVICE} (index=${CAMERA_INDEX}, ${WIDTH}x${HEIGHT}@${FPS}fps)"
echo "  ROS topic      : ${IMAGE_TOPIC}"

docker run "${ARGS[@]}" \
  "${IMAGE_NAME}" \
  ros2 launch video_streaming stream_only.launch.py \
    width:="${WIDTH}" \
    height:="${HEIGHT}" \
    fps:="${FPS}" \
    camera_index:="${CAMERA_INDEX}" \
    image_topic:="${IMAGE_TOPIC}"
