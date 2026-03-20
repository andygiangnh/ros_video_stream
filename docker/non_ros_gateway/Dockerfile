FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir aiohttp aiortc av numpy

WORKDIR /app
COPY docker/non_ros_gateway/gateway.py /app/gateway.py
COPY video_streaming/www /app/www

EXPOSE 8080
CMD ["python", "/app/gateway.py"]
