#!/usr/bin/env bash
set -e

IMAGE_NAME="${IMAGE_NAME:-video_streaming:humble}"

docker build -f docker/Dockerfile -t "${IMAGE_NAME}" .
