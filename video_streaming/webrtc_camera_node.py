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

    def _run_event_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _start_server(self):
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

    async def _recording_start(self, _request):
        return web.json_response(
            {"success": False, "message": "Recording is available only in RTMP gateway mode"},
            status=400,
        )

    async def _recording_stop(self, _request):
        return web.json_response(
            {"success": False, "message": "Recording is available only in RTMP gateway mode"},
            status=400,
        )

    async def _recording_status(self, _request):
        return web.json_response({"recording": False, "message": "Recording is available only in RTMP gateway mode"})

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
