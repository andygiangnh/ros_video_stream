import argparse
import asyncio
from fractions import Fraction
import os
import threading
import time
from pathlib import Path

import cv2
import rclpy
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from aiohttp import web
from av import VideoFrame
from rclpy.node import Node
from sensor_msgs.msg import Image as RosImage
from std_msgs.msg import Header
from std_srvs.srv import SetBool


class OpenCVCameraTrack(VideoStreamTrack):
    def __init__(self, camera_index: int, width: int, height: int, fps: int):
        super().__init__()
        self._capture = cv2.VideoCapture(camera_index)
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._capture.set(cv2.CAP_PROP_FPS, fps)
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        self._width = width
        self._height = height
        self._fps = fps
        self._time_base = Fraction(1, max(fps, 1))
        self._pts = 0
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)

        if not self._capture.isOpened():
            raise RuntimeError(f"Unable to open camera index {camera_index}")

        self._reader_thread.start()

    def _reader_loop(self):
        while self._running:
            ok, frame_bgr = self._capture.read()
            if not ok:
                time.sleep(0.01)
                continue

            if frame_bgr.shape[1] != self._width or frame_bgr.shape[0] != self._height:
                frame_bgr = cv2.resize(frame_bgr, (self._width, self._height))

            with self._frame_lock:
                self._latest_frame = frame_bgr

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        frame_bgr = None
        for _ in range(20):
            with self._frame_lock:
                if self._latest_frame is not None:
                    frame_bgr = self._latest_frame.copy()
                    break
            await asyncio.sleep(0.005)

        if frame_bgr is None:
            raise RuntimeError("Camera frame read failed")

        frame = VideoFrame.from_ndarray(frame_bgr, format="bgr24")
        frame.pts = pts if pts is not None else self._pts
        frame.time_base = time_base if time_base is not None else self._time_base
        self._pts += 1
        return frame

    def close(self):
        self._running = False
        if self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class WebRTCCameraNode(Node):
    def __init__(self, host: str, port: int, camera_index: int, width: int, height: int, fps: int):
        super().__init__("webrtc_camera_node")
        self._host = host
        self._port = port
        self._camera_index = camera_index
        self._width = width
        self._height = height
        self._fps = fps

        # ---- Camera track + WebRTC relay ------------------------------------
        self._camera_track = OpenCVCameraTrack(
            camera_index=self._camera_index,
            width=self._width,
            height=self._height,
            fps=self._fps,
        )
        self._relay = MediaRelay()

        # ---- ROS 2 publisher: frames for recorder node ----------------------
        self._image_pub = self.create_publisher(RosImage, "/camera/image_raw", 10)
        self.create_timer(1.0 / max(fps, 1), self._publish_latest_frame)

        # ---- ROS 2 service client: control recorder node -------------------
        self._record_client = self.create_client(SetBool, "/video_recorder_node/set_recording")
        self._is_recording_active = False

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

        # WebRTC signaling
        self._app.router.add_post("/offer", self._offer)

        # Recording control endpoints
        self._app.router.add_post("/recording/start", self._recording_start)
        self._app.router.add_post("/recording/stop",  self._recording_stop)
        self._app.router.add_get("/recording/status", self._recording_status)

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

    # ---- Frame publishing ---------------------------------------------------

    def _publish_latest_frame(self) -> None:
        """Timer callback: publish latest frame as sensor_msgs/Image for recorder."""
        with self._camera_track._frame_lock:
            if self._camera_track._latest_frame is None:
                return
            frame_bgr = self._camera_track._latest_frame.copy()

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
        self._image_pub.publish(msg)

    # ---- Recording (async ROS 2 service calls from aiohttp context) ---------

    async def _call_record_service(self, start: bool) -> dict:
        """Bridge async aiohttp context → sync ROS 2 service call."""
        if not self._record_client.wait_for_service(timeout_sec=1.0):
            return {"success": False, "message": "Recorder service unavailable. Is video_recorder_node running?"}

        req = SetBool.Request()
        req.data = start

        result_event = asyncio.Event()
        result_holder: list = [None]

        ros_future = self._record_client.call_async(req)

        def on_done(future):
            try:
                result_holder[0] = future.result()
            except Exception as e:
                self.get_logger().warn(f"Recording service error: {e}")
            self._loop.call_soon_threadsafe(result_event.set)

        ros_future.add_done_callback(on_done)

        try:
            await asyncio.wait_for(result_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            return {"success": False, "message": "Service call timed out"}

        result = result_holder[0]
        if result is None:
            return {"success": False, "message": "Service call failed"}

        if result.success:
            self._is_recording_active = start

        return {"success": result.success, "message": result.message}

    def _run_event_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _start_server(self):
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

    async def _recording_start(self, _request):
        result = await self._call_record_service(True)
        return web.json_response(result)

    async def _recording_stop(self, _request):
        result = await self._call_record_service(False)
        return web.json_response(result)

    async def _recording_status(self, _request):
        return web.json_response({"recording": self._is_recording_active})

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

        self._camera_track.close()

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        super().destroy_node()


def parse_args():
    import sys
    from rclpy.utilities import remove_ros_args
    parser = argparse.ArgumentParser(description="ROS 2 WebRTC camera streamer node")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    return parser.parse_args(remove_ros_args(sys.argv[1:]))


def main(args=None):
    cli = parse_args()

    rclpy.init(args=args)
    node = WebRTCCameraNode(
        host=cli.host,
        port=cli.port,
        camera_index=cli.camera_index,
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
