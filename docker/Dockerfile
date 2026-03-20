FROM ros:humble-ros-base

SHELL ["/bin/bash", "-c"]
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-colcon-common-extensions \
    python3-opencv \
    python3-pip \
    ros-humble-cv-bridge \
    ros-humble-sensor-msgs \
    ros-humble-image-transport \
    ros-humble-launch-ros \
    ffmpeg \
    v4l-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir aiortc aiohttp av

WORKDIR /ws/src/video_streaming
COPY . /ws/src/video_streaming

WORKDIR /ws
RUN source /opt/ros/humble/setup.bash \
    && colcon build --packages-select video_streaming

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "launch", "video_streaming", "stream_only.launch.py"]
