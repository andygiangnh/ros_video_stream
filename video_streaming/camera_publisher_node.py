import argparse
import threading
import time

import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image as RosImage
from std_msgs.msg import Header


class CameraPublisherNode(Node):
    def __init__(
        self,
        camera_index: int,
        width: int,
        height: int,
        fps: int,
        output_topic: str,
    ):
        super().__init__("camera_publisher_node")
        self._width = width
        self._height = height
        self._fps = max(fps, 1)

        self._pub = self.create_publisher(RosImage, output_topic, 10)
        self._capture = cv2.VideoCapture(camera_index)
        self._camera_index = camera_index
        self._consecutive_read_failures = 0
        self._configure_capture(self._capture)

        if not self._capture.isOpened():
            raise RuntimeError(f"Unable to open camera index {camera_index}")

        self._running = True
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

        self.create_timer(1.0 / self._fps, self._publish_latest_frame)
        self.get_logger().info(
            f"Publishing camera index={camera_index} to topic '{output_topic}' at {width}x{height}@{self._fps}"
        )

    def _configure_capture(self, capture: cv2.VideoCapture):
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        capture.set(cv2.CAP_PROP_FPS, self._fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

    def _reopen_camera(self):
        self.get_logger().warn(f"Reopening camera index {self._camera_index} after read failures")
        try:
            if self._capture is not None:
                self._capture.release()
        except Exception:
            pass

        self._capture = cv2.VideoCapture(self._camera_index)
        self._configure_capture(self._capture)

        if not self._capture.isOpened():
            self.get_logger().warn(f"Camera reopen failed for index {self._camera_index}")

    def _reader_loop(self):
        while self._running:
            ok, frame_bgr = self._capture.read()
            if not ok:
                self._consecutive_read_failures += 1
                if self._consecutive_read_failures >= 30:
                    self._consecutive_read_failures = 0
                    self._reopen_camera()
                time.sleep(0.01)
                continue

            self._consecutive_read_failures = 0

            if frame_bgr.shape[1] != self._width or frame_bgr.shape[0] != self._height:
                frame_bgr = cv2.resize(frame_bgr, (self._width, self._height))

            with self._frame_lock:
                self._latest_frame = frame_bgr

    def _publish_latest_frame(self):
        with self._frame_lock:
            if self._latest_frame is None:
                return
            frame_bgr = self._latest_frame.copy()

        msg = RosImage()
        msg.header = Header()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "camera"
        msg.height = self._height
        msg.width = self._width
        msg.encoding = "bgr8"
        msg.is_bigendian = False
        msg.step = self._width * 3
        msg.data = frame_bgr.tobytes()
        self._pub.publish(msg)

    def destroy_node(self):
        self._running = False
        if self._reader.is_alive():
            self._reader.join(timeout=1)
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        super().destroy_node()


def parse_args():
    import sys
    from rclpy.utilities import remove_ros_args

    parser = argparse.ArgumentParser(description="ROS 2 camera publisher node")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--output-topic", default="/camera/image_raw")
    return parser.parse_args(remove_ros_args(sys.argv[1:]))


def main(args=None):
    cli = parse_args()
    rclpy.init(args=args)
    node = CameraPublisherNode(
        camera_index=cli.camera_index,
        width=cli.width,
        height=cli.height,
        fps=cli.fps,
        output_topic=cli.output_topic,
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
