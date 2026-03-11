import argparse
import asyncio
from fractions import Fraction
import threading
import time

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
        self._camera_track = OpenCVCameraTrack(
            camera_index=self._camera_index,
            width=self._width,
            height=self._height,
            fps=self._fps,
        )
        self._relay = MediaRelay()

        self._pcs = set()
        self._app = web.Application()
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/client.js", self._client_js)
        self._app.router.add_post("/offer", self._offer)

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

    async def _index(self, _request):
        html = """
<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <title>ROS2 WebRTC Camera</title>
    <style>
      body { font-family: sans-serif; margin: 24px; }
      video { width: min(100%, 960px); border: 1px solid #bbb; border-radius: 8px; }
      button { margin-right: 8px; }
    </style>
  </head>
  <body>
    <h2>ROS 2 Camera Stream (WebRTC)</h2>
    <button id=\"start\">Start</button>
    <button id=\"stop\">Stop</button>
    <p id=\"status\">Idle</p>
        <p id=\"metrics\">FPS: -- | RTT: -- ms | Jitter Buffer: -- ms</p>
    <video id=\"video\" autoplay playsinline controls></video>
    <script src=\"/client.js\"></script>
  </body>
</html>
"""
        return web.Response(text=html, content_type="text/html")

    async def _client_js(self, _request):
        script = """
let pc = null;
let statsTimer = null;
let decodedFps = null;
let smoothedFps = null;
let lastFrameNow = null;

function setMetrics({ fps = null, rttMs = null, jitterBufferMs = null } = {}) {
    const fmt = (value, suffix = '') => (value == null || Number.isNaN(value) ? '--' : `${value.toFixed(1)}${suffix}`);
    document.getElementById('metrics').textContent =
        `FPS: ${fmt(fps)} | RTT: ${fmt(rttMs, ' ms')} | Jitter Buffer: ${fmt(jitterBufferMs, ' ms')}`;
}

function startFrameMeter(videoElement) {
    if (!videoElement.requestVideoFrameCallback) {
        return;
    }

    const onFrame = (now) => {
        if (lastFrameNow !== null && now > lastFrameNow) {
            const instantFps = 1000 / (now - lastFrameNow);
            smoothedFps = smoothedFps === null ? instantFps : (smoothedFps * 0.8 + instantFps * 0.2);
            decodedFps = smoothedFps;
        }
        lastFrameNow = now;
        if (pc) {
            videoElement.requestVideoFrameCallback(onFrame);
        }
    };

    videoElement.requestVideoFrameCallback(onFrame);
}

async function pollStats() {
    if (!pc) {
        return;
    }

    const stats = await pc.getStats();
    let rttMs = null;
    let jitterBufferMs = null;
    let statsFps = null;

    stats.forEach((report) => {
        if (
            report.type === 'candidate-pair' &&
            report.state === 'succeeded' &&
            report.nominated &&
            typeof report.currentRoundTripTime === 'number'
        ) {
            rttMs = report.currentRoundTripTime * 1000;
        }

        if (report.type === 'inbound-rtp' && report.kind === 'video') {
            if (
                typeof report.jitterBufferDelay === 'number' &&
                typeof report.jitterBufferEmittedCount === 'number' &&
                report.jitterBufferEmittedCount > 0
            ) {
                jitterBufferMs = (report.jitterBufferDelay / report.jitterBufferEmittedCount) * 1000;
            }

            if (typeof report.framesPerSecond === 'number') {
                statsFps = report.framesPerSecond;
            }
        }
    });

    setMetrics({ fps: statsFps ?? decodedFps, rttMs, jitterBufferMs });
}

async function start() {
  if (pc) {
    return;
  }

  document.getElementById('status').textContent = 'Starting...';
    setMetrics();
    decodedFps = null;
    smoothedFps = null;
    lastFrameNow = null;
  pc = new RTCPeerConnection();
  pc.addTransceiver('video', { direction: 'recvonly' });

  pc.ontrack = (event) => {
    const video = document.getElementById('video');
    video.srcObject = event.streams[0];
        startFrameMeter(video);
  };

    try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        const response = await fetch('/offer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
        });

        const answer = await response.json();
        await pc.setRemoteDescription(answer);

        if (statsTimer) {
            clearInterval(statsTimer);
        }
        statsTimer = setInterval(() => {
            pollStats().catch(() => {});
        }, 1000);

        document.getElementById('status').textContent = 'Streaming';
    } catch (err) {
        document.getElementById('status').textContent = `Error: ${err}`;
        stop();
    }
}

function stop() {
    if (statsTimer) {
        clearInterval(statsTimer);
        statsTimer = null;
    }

  if (pc) {
    pc.close();
    pc = null;
  }
  document.getElementById('status').textContent = 'Stopped';
    setMetrics();
}

document.getElementById('start').onclick = start;
document.getElementById('stop').onclick = stop;
"""
        return web.Response(text=script, content_type="application/javascript")

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
    parser = argparse.ArgumentParser(description="ROS 2 WebRTC camera streamer node")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    return parser.parse_args()


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
