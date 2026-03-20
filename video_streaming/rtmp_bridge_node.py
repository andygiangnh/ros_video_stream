import argparse
import subprocess
import threading

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image


class RtmpBridgeNode(Node):
    def __init__(self, input_topic: str, width: int, height: int, fps: int, rtmp_url: str):
        super().__init__("rtmp_bridge_node")
        self._width = width
        self._height = height
        self._fps = max(fps, 1)
        self._rtmp_url = rtmp_url

        self._ffmpeg_lock = threading.Lock()
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._frame_count = 0
        self._last_log_frame_count = 0

        self._sub = self.create_subscription(Image, input_topic, self._image_callback, 10)
        self._start_ffmpeg()
        
        # Log frame count periodically
        self.create_timer(2.0, self._log_frame_count)

        self.get_logger().info(
            f"Bridging topic '{input_topic}' -> RTMP '{self._rtmp_url}' at {self._width}x{self._height}@{self._fps}"
        )

    def _build_ffmpeg_cmd(self) -> list[str]:
        return [
            "ffmpeg",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{self._width}x{self._height}",
            "-r",
            str(self._fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "baseline",
            "-level",
            "3.1",
            "-crf",
            "28",
            "-g",
            str(self._fps),
            "-keyint_min",
            str(self._fps),
            "-bf",
            "0",
            "-f",
            "flv",
            self._rtmp_url,
        ]

    def _start_ffmpeg(self):
        with self._ffmpeg_lock:
            if self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is None:
                return
            try:
                self._ffmpeg_proc = subprocess.Popen(
                    self._build_ffmpeg_cmd(),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError as exc:
                raise RuntimeError("ffmpeg not found in runtime") from exc

    def _restart_ffmpeg_if_needed(self):
        with self._ffmpeg_lock:
            if self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is None:
                return
            self.get_logger().warn("FFmpeg exited, restarting RTMP bridge process")
            self._ffmpeg_proc = None
        self._start_ffmpeg()

    def _image_callback(self, msg: Image):
        self._restart_ffmpeg_if_needed()
        with self._ffmpeg_lock:
            if self._ffmpeg_proc is None or self._ffmpeg_proc.stdin is None:
                return
            try:
                self._ffmpeg_proc.stdin.write(bytes(msg.data))
                self._ffmpeg_proc.stdin.flush()
                self._frame_count += 1
            except BrokenPipeError:
                self.get_logger().warn("FFmpeg stdin pipe broken; bridge will auto-restart")
                self._ffmpeg_proc = None
            except Exception as exc:
                self.get_logger().warn(f"Failed writing frame to FFmpeg: {exc}")

    def _log_frame_count(self):
        if self._frame_count > self._last_log_frame_count:
            fps = (self._frame_count - self._last_log_frame_count) / 2.0  # 2 second interval
            self.get_logger().info(f"RTMP bridge flowing {fps:.1f} fps ({self._frame_count} total frames)")
            self._last_log_frame_count = self._frame_count
        else:
            self.get_logger().warn("RTMP bridge: NO FRAMES RECEIVED from topic")

    def destroy_node(self):
        with self._ffmpeg_lock:
            proc = self._ffmpeg_proc
            self._ffmpeg_proc = None

        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait()

        super().destroy_node()


def parse_args():
    import sys
    from rclpy.utilities import remove_ros_args

    parser = argparse.ArgumentParser(description="ROS 2 image topic to RTMP bridge")
    parser.add_argument("--input-topic", default="/camera/image_raw")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--rtmp-url", default="rtmp://localhost:1935/stream/stream")
    return parser.parse_args(remove_ros_args(sys.argv[1:]))


def main(args=None):
    cli = parse_args()

    rclpy.init(args=args)
    node = RtmpBridgeNode(
        input_topic=cli.input_topic,
        width=cli.width,
        height=cli.height,
        fps=cli.fps,
        rtmp_url=cli.rtmp_url,
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
