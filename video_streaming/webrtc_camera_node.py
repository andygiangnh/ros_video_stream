import argparse
import asyncio
from fractions import Fraction
import threading
from pathlib import Path

import cv2
import rclpy
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from aiohttp import web
from av import VideoFrame
from cv_bridge import CvBridge
from sensor_msgs.msg import Image
from rclpy.node import Node


class RosImageTrack(VideoStreamTrack):
    def __init__(self, node: "WebRTCCameraNode", width: int, height: int, fps: int):
        super().__init__()
        self._node = node
        self._width = width
        self._height = height
        self._fps = fps
        self._time_base = Fraction(1, max(fps, 1))
        self._pts = 0

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        frame_bgr = None
        for _ in range(20):
            frame_bgr = self._node.get_latest_frame()
            if frame_bgr is not None:
                break
            await asyncio.sleep(0.005)

        if frame_bgr is None:
            raise RuntimeError("No ROS image frames available")

        frame = VideoFrame.from_ndarray(frame_bgr, format="bgr24")
        frame.pts = pts if pts is not None else self._pts
        frame.time_base = time_base if time_base is not None else self._time_base
        self._pts += 1
        return frame


class WebRTCCameraNode(Node):
    def __init__(self, host: str, port: int, image_topic: str, width: int, height: int, fps: int):
        super().__init__("webrtc_camera_node")
        self._host = host
        self._port = port
        self._image_topic = image_topic
        self._width = width
        self._height = height
        self._fps = fps
        self._bridge = CvBridge()
        self._latest_frame = None
        self._frame_lock = threading.Lock()

        self._sub = self.create_subscription(Image, self._image_topic, self._image_callback, 10)

        # ---- ROS image track + WebRTC relay ---------------------------------
        self._camera_track = RosImageTrack(self, width=self._width, height=self._height, fps=self._fps)
        self._relay = MediaRelay()

        # ---- aiohttp web application ----------------------------------------
        self._pcs = set()
        self._app = web.Application()

        possible_www_dirs = [Path(__file__).parent / "www"]
        www_dir = next((str(p) for p in possible_www_dirs if p.is_dir()), None)

        if www_dir:
            async def root_handler(request):
                return web.FileResponse(Path(www_dir) / "index.html")
            self._app.router.add_get("/", root_handler)
            self._app.router.add_static("/", www_dir, name="static")
            self.get_logger().info(f"Serving static files from: {www_dir}")
        else:
            self.get_logger().warn("Static www/ directory not found")

        self._app.router.add_get("/config", self._config)
        # WebRTC signaling
        self._app.router.add_post("/offer", self._offer)

        # ---- Start aiohttp event loop in background thread ------------------
        self._runner = web.AppRunner(self._app)
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self._loop_thread.start()

        startup = asyncio.run_coroutine_threadsafe(self._start_server(), self._loop)
        startup.result(timeout=10)

        self.get_logger().info(
            f"WebRTC signaling server started at http://{self._host}:{self._port}"
        )
        self.get_logger().info(
            f"Subscribing to ROS image topic '{self._image_topic}' at {self._width}x{self._height}@{self._fps}"
        )

    def _run_event_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _image_callback(self, msg: Image):
        try:
            frame_bgr = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as exc:
            self.get_logger().warn(f"Failed to convert ROS image from '{self._image_topic}': {exc}")
            return

        if frame_bgr.shape[1] != self._width or frame_bgr.shape[0] != self._height:
            frame_bgr = cv2.resize(frame_bgr, (self._width, self._height))

        with self._frame_lock:
            self._latest_frame = frame_bgr

    def get_latest_frame(self):
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    async def _start_server(self):
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

    async def _config(self, _request):
        return web.json_response({"iceServers": []})

    async def _offer(self, request):
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])

        pc = RTCPeerConnection()
        self._pcs.add(pc)

        @pc.on("connectionstatechange")
        async def _on_connectionstatechange():
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await pc.close()
                self._pcs.discard(pc)

        pc.addTrack(self._relay.subscribe(self._camera_track))

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.json_response(
            {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
            }
        )

    def destroy_node(self):
        async def _shutdown_async():
            for pc in list(self._pcs):
                await pc.close()
                self._pcs.discard(pc)
            await self._runner.cleanup()

        shutdown = asyncio.run_coroutine_threadsafe(_shutdown_async(), self._loop)
        shutdown.result(timeout=10)

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        super().destroy_node()


def parse_args():
    import sys
    from rclpy.utilities import remove_ros_args
    parser = argparse.ArgumentParser(description="ROS 2 WebRTC camera streamer node")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--image-topic", default="/camera/image_raw")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=15)
    return parser.parse_args(remove_ros_args(sys.argv[1:]))


def main(args=None):
    cli = parse_args()

    rclpy.init(args=args)
    node = WebRTCCameraNode(
        host=cli.host,
        port=cli.port,
        image_topic=cli.image_topic,
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
