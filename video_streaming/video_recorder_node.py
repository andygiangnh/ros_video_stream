"""
ROS 2 Video Recorder Node (lifecycle-style).

Receives camera frames from a sensor_msgs/Image topic and pipes them to an
FFmpeg subprocess that encodes to MP4 in a mounted local directory.

Controlled via the std_srvs/SetBool service '~/set_recording':
  True  -> start a new recording
  False -> stop and finalize the current recording

Architecture note:
  This is implemented as a plain rclpy Node for simplicity. It can be
  converted to a rclpy.lifecycle.LifecycleNode where on_activate() calls
  _start_recording() and on_deactivate() calls _stop_recording(), allowing
  lifecycle managers to drive recording state via lifecycle transitions.
"""

import argparse
import datetime
import os
import subprocess
import threading

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_srvs.srv import SetBool


class VideoRecorderNode(Node):
    def __init__(self, output_dir: str, width: int, height: int, fps: int, input_topic: str):
        super().__init__("video_recorder_node")

        self._output_dir = output_dir
        self._width = width
        self._height = height
        self._fps = fps

        self._ffmpeg_proc: subprocess.Popen | None = None
        self._ffmpeg_lock = threading.Lock()
        self._is_recording = False
        self._current_file: str | None = None

        # Service: True = start recording, False = stop recording
        self._record_srv = self.create_service(
            SetBool,
            "~/set_recording",
            self._set_recording_callback,
        )

        # Subscribe to camera frames published by webrtc_camera_node
        self._image_sub = self.create_subscription(
            Image,
            input_topic,
            self._image_callback,
            10,
        )

        self.get_logger().info(
            f"VideoRecorderNode ready. Output: '{output_dir}'. Topic: '{input_topic}'."
        )

    # ---- ROS 2 service handler ------------------------------------------------

    def _set_recording_callback(
        self, request: SetBool.Request, response: SetBool.Response
    ) -> SetBool.Response:
        if request.data:
            ok, msg = self._start_recording()
        else:
            ok, msg = self._stop_recording()
        response.success = ok
        response.message = msg
        return response

    # ---- Recording state machine ---------------------------------------------

    def _start_recording(self) -> tuple[bool, str]:
        with self._ffmpeg_lock:
            if self._is_recording:
                return False, "Already recording"

            try:
                os.makedirs(self._output_dir, exist_ok=True)
            except OSError as e:
                return False, f"Cannot create output directory: {e}"

            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self._current_file = os.path.join(
                self._output_dir, f"recording_{timestamp}.mp4"
            )

            cmd = [
                "ffmpeg", "-y",
                # Input: raw BGR frames piped from ROS 2 image topic
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-s", f"{self._width}x{self._height}",
                "-r", str(self._fps),
                "-i", "pipe:0",
                # Output: H.264 MP4 with fast-start for web playback
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-movflags", "+faststart",
                self._current_file,
            ]
            cmd = [
                "ffmpeg", "-y",
                # Input: raw BGR frames piped from ROS 2 image topic
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-s", f"{self._width}x{self._height}",
                "-r", str(self._fps),
                "-i", "pipe:0",
                # Output: H.264 MP4 — yuv420p is required for broad player/browser compatibility
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                self._current_file,
            ]

            try:
                self._ffmpeg_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                self._is_recording = True
                self.get_logger().info(f"Recording started → {self._current_file}")
                return True, f"Recording to {self._current_file}"
            except FileNotFoundError:
                self._current_file = None
                return False, "ffmpeg not found — install it with: apt-get install ffmpeg"
            except Exception as e:
                self._current_file = None
                return False, f"Failed to start FFmpeg: {e}"

    def _stop_recording(self) -> tuple[bool, str]:
        with self._ffmpeg_lock:
            if not self._is_recording:
                return False, "Not currently recording"

            self._is_recording = False
            proc = self._ffmpeg_proc
            self._ffmpeg_proc = None

            if proc and proc.stdin:
                try:
                    proc.stdin.close()
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                except Exception:
                    pass

            saved = self._current_file
            self._current_file = None
            self.get_logger().info(f"Recording stopped → {saved}")
            return True, f"Saved: {saved}"

    # ---- Frame consumer ------------------------------------------------------

    def _image_callback(self, msg: Image) -> None:
        with self._ffmpeg_lock:
            if not self._is_recording or self._ffmpeg_proc is None:
                return

            if self._ffmpeg_proc.poll() is not None:
                self._is_recording = False
                self.get_logger().warn("FFmpeg exited unexpectedly; recording stopped")
                return

            try:
                self._ffmpeg_proc.stdin.write(bytes(msg.data))
            except BrokenPipeError:
                self._is_recording = False
                self.get_logger().warn("FFmpeg pipe broken; recording stopped")
            except Exception as e:
                self.get_logger().warn(f"Frame write error: {e}")

    # ---- Properties ----------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def current_file(self) -> str | None:
        return self._current_file

    # ---- Cleanup -------------------------------------------------------------

    def destroy_node(self) -> None:
        self._stop_recording()
        super().destroy_node()


def main(args=None):
    import sys
    from rclpy.utilities import remove_ros_args
    parser = argparse.ArgumentParser(description="ROS 2 FFmpeg video recorder node")
    parser.add_argument("--output-dir", default="/recordings",
                        help="Directory where MP4 files are saved (mount host $HOME/video here)")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--input-topic", default="/camera/image_raw")
    cli = parser.parse_args(remove_ros_args(sys.argv[1:]))

    rclpy.init(args=args)
    node = VideoRecorderNode(
        output_dir=cli.output_dir,
        width=cli.width,
        height=cli.height,
        fps=cli.fps,
        input_topic=cli.input_topic,
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
