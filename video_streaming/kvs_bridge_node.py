"""
ROS 2 node to stream camera frames to AWS Kinesis Video Streams.
Captures frames from ROS image topic and puts them to KVS stream.
"""

import argparse
import asyncio
import threading
import time
from pathlib import Path

import cv2
import rclpy
from av import VideoFrame
from cv_bridge import CvBridge
from sensor_msgs.msg import Image
from rclpy.node import Node

import boto3


class KVSBridgeNode(Node):
    """ROS 2 node that bridges camera frames to AWS Kinesis Video Streams."""

    def __init__(
        self,
        image_topic: str,
        stream_name: str,
        aws_region: str,
        width: int,
        height: int,
        fps: int,
    ):
        super().__init__("kvs_bridge_node")
        
        self._image_topic = image_topic
        self._stream_name = stream_name
        self._aws_region = aws_region
        self._width = width
        self._height = height
        self._fps = max(fps, 1)
        self._bridge = CvBridge()
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._running = True

        # ROS subscription
        self._sub = self.create_subscription(
            Image, self._image_topic, self._image_callback, 10
        )

        # AWS KVS client
        self._kvs_client = boto3.client("kinesisvideo", region_name=self._aws_region)
        self._kvs_media_client = None
        self._stream_arn = None

        # Ensure stream exists
        self._ensure_stream_exists()

        # Frame writer thread
        self._writer_thread = threading.Thread(target=self._write_frames_loop, daemon=True)
        self._writer_thread.start()

        self.get_logger().info(
            f"KVS Bridge initialized: subscribing to '{self._image_topic}' "
            f"and streaming to KVS '{self._stream_name}' in {self._aws_region}"
        )

    def _ensure_stream_exists(self):
        """Create KVS stream if it doesn't exist."""
        try:
            # Check if stream exists
            response = self._kvs_client.describe_stream(StreamName=self._stream_name)
            self._stream_arn = response["StreamInfo"]["StreamARN"]
            self.get_logger().info(f"Using existing KVS stream: {self._stream_arn}")
        except self._kvs_client.exceptions.ResourceNotFoundException:
            # Create new stream
            self.get_logger().info(f"Creating new KVS stream: {self._stream_name}")
            response = self._kvs_client.create_stream(
                StreamName=self._stream_name,
                MediaStreamType="REALTIME",
                DataRetentionInHours=1,
            )
            self._stream_arn = response["StreamInfo"]["StreamARN"]

    def _get_put_media_endpoint(self):
        """Get the endpoint for putting media to the stream."""
        info = self._kvs_client.get_data_endpoint(
            StreamName=self._stream_name, APIName="PUT_MEDIA"
        )
        return info["DataEndpoint"]

    def _image_callback(self, msg: Image):
        """ROS image subscription callback."""
        try:
            frame_bgr = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as exc:
            self.get_logger().warn(
                f"Failed to convert ROS image from '{self._image_topic}': {exc}"
            )
            return

        # Resize if needed
        if frame_bgr.shape[1] != self._width or frame_bgr.shape[0] != self._height:
            frame_bgr = cv2.resize(frame_bgr, (self._width, self._height))

        with self._frame_lock:
            self._latest_frame = frame_bgr

    def _write_frames_loop(self):
        """Background thread that writes frames to KVS."""
        time.sleep(1)  # Wait for stream setup

        put_media_endpoint = self._get_put_media_endpoint()
        self._kvs_media_client = boto3.client(
            "kinesis-video-media", endpoint_url=put_media_endpoint, region_name=self._aws_region
        )

        frame_count = 0
        last_report_time = time.time()

        while self._running:
            with self._frame_lock:
                frame_bgr = self._latest_frame
                if frame_bgr is None:
                    time.sleep(0.01)
                    continue
                frame_bgr = frame_bgr.copy()

            try:
                # Convert BGR to H264 encoded frame
                # In production, use actual H264 encoder; for demo, send raw frames
                ret, buffer = cv2.imencode(".jpg", frame_bgr)
                if not ret:
                    continue

                # Put media to KVS
                self._kvs_media_client.put_media(
                    StreamName=self._stream_name,
                    ContentType="video/h264",  # Or image/jpeg for raw frames
                    Payload=buffer.tobytes(),
                )

                frame_count += 1
                now = time.time()
                if now - last_report_time >= 5:
                    fps = frame_count / (now - last_report_time)
                    self.get_logger().info(
                        f"KVS: streaming {fps:.1f} fps ({frame_count} frames total)"
                    )
                    frame_count = 0
                    last_report_time = now

            except Exception as e:
                self.get_logger().warn(f"Error putting media to KVS: {e}")
                # Reconnect
                time.sleep(1)
                try:
                    put_media_endpoint = self._get_put_media_endpoint()
                    self._kvs_media_client = boto3.client(
                        "kinesis-video-media",
                        endpoint_url=put_media_endpoint,
                        region_name=self._aws_region,
                    )
                except Exception as e2:
                    self.get_logger().error(f"Failed to reconnect to KVS: {e2}")

            # Frame pacing
            time.sleep(1.0 / self._fps)

    def destroy_node(self):
        """Clean up resources."""
        self._running = False
        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=2)
        super().destroy_node()


def parse_args():
    """Parse command-line arguments."""
    import sys
    from rclpy.utilities import remove_ros_args

    parser = argparse.ArgumentParser(
        description="ROS 2 camera to AWS Kinesis Video Stream bridge"
    )
    parser.add_argument("--image-topic", default="/camera/image_raw")
    parser.add_argument("--stream-name", default="ros2-camera-stream")
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=15)
    return parser.parse_args(remove_ros_args(sys.argv[1:]))


def main(args=None):
    """Main entry point."""
    cli = parse_args()
    rclpy.init(args=args)
    node = KVSBridgeNode(
        image_topic=cli.image_topic,
        stream_name=cli.stream_name,
        aws_region=cli.aws_region,
        width=cli.width,
        height=cli.height,
        fps=cli.fps,
    )

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
