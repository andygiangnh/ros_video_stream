import asyncio
import datetime
import os
import subprocess
import threading
import time
import logging
from fractions import Fraction
from pathlib import Path

import numpy as np
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.rtcconfiguration import RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRelay
from av import VideoFrame


class RtmpVideoTrack(VideoStreamTrack):
    def __init__(self, rtmp_url: str, width: int, height: int, fps: int):
        super().__init__()
        self._rtmp_url = rtmp_url
        self._width = width
        self._height = height
        self._fps = max(fps, 1)

        self._time_base = Fraction(1, self._fps)
        self._pts = 0
        self._latest_frame = None
        self._latest_frame_ts = 0.0
        self._frame_lock = threading.Lock()
        self._running = True
        self._frame_size = self._width * self._height * 3
        self._restart_requested = False
        self._restart_count = 0
        self._frame_count = 0
        self._fallback_frame = np.zeros((self._height, self._width, 3), dtype=np.uint8)
        self._logger = logging.getLogger("webrtc-gateway.track")

        self._ffmpeg_proc: subprocess.Popen | None = None
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def _read_exact(self, stream, size: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while total < size:
            chunk = stream.read(size - total)
            if not chunk:
                return b""
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    def _start_ffmpeg(self):
        self._stop_ffmpeg()
        cmd = [
            "ffmpeg",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-i",
            self._rtmp_url,
            "-an",
            "-vf",
            f"scale={self._width}:{self._height}",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        self._ffmpeg_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        self._logger.info("Started RTMP reader: %s", self._rtmp_url)

    def _stop_ffmpeg(self):
        if self._ffmpeg_proc is None:
            return

        try:
            self._ffmpeg_proc.kill()
            self._ffmpeg_proc.wait(timeout=2)
        except Exception:
            pass
        self._ffmpeg_proc = None

    def _request_restart(self):
        self._restart_requested = True
        self._restart_count += 1

    def _reader_loop(self):
        while self._running:
            try:
                if self._restart_requested:
                    self._restart_requested = False
                    self._stop_ffmpeg()

                if self._ffmpeg_proc is None:
                    self._start_ffmpeg()
                    time.sleep(0.2)
                    continue

                if self._ffmpeg_proc.poll() is not None:
                    self._stop_ffmpeg()
                    time.sleep(0.2)
                    continue

                if self._ffmpeg_proc.stdout is None:
                    self._stop_ffmpeg()
                    time.sleep(0.2)
                    continue

                frame_bytes = self._read_exact(self._ffmpeg_proc.stdout, self._frame_size)
                if not frame_bytes:
                    self._stop_ffmpeg()
                    time.sleep(0.2)
                    continue

                frame_bgr = np.frombuffer(frame_bytes, dtype=np.uint8).reshape(
                    (self._height, self._width, 3)
                )

                with self._frame_lock:
                    self._latest_frame = frame_bgr.copy()
                    self._latest_frame_ts = time.time()
                    self._frame_count += 1
            except Exception:
                self._stop_ffmpeg()
                time.sleep(0.2)
                continue

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        frame_bgr = None
        frame_ts = 0.0
        for _ in range(20):
            with self._frame_lock:
                if self._latest_frame is not None:
                    frame_bgr = self._latest_frame.copy()
                    frame_ts = self._latest_frame_ts
                    break
            await asyncio.sleep(0.01)

        if frame_bgr is None:
            frame_bgr = self._fallback_frame
            self._request_restart()
        else:
            age = time.time() - frame_ts
            if age > 0.5:
                self._logger.warning("Stale frame detected (age=%.2fs), restarting RTMP reader", age)
                self._request_restart()

        frame = VideoFrame.from_ndarray(frame_bgr, format="bgr24")
        frame.pts = pts if pts is not None else self._pts
        frame.time_base = time_base if time_base is not None else self._time_base
        self._pts += 1
        return frame

    def close(self):
        self._running = False
        if self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)
        self._stop_ffmpeg()

    def snapshot(self) -> dict:
        with self._frame_lock:
            ts = self._latest_frame_ts
            count = self._frame_count

        age = None
        if ts > 0:
            age = max(0.0, time.time() - ts)

        return {
            "has_frame": ts > 0,
            "frame_age_sec": age,
            "frame_count": count,
            "restart_count": self._restart_count,
            "rtmp_url": self._rtmp_url,
            "resolution": f"{self._width}x{self._height}",
            "fps": self._fps,
        }

    def get_latest_frame(self):
        with self._frame_lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()


class GatewayRecorder:
    def __init__(self, track: RtmpVideoTrack, record_dir: str, width: int, height: int, fps: int):
        self._track = track
        self._record_dir = record_dir
        self._width = width
        self._height = height
        self._fps = max(fps, 1)
        self._logger = logging.getLogger("webrtc-gateway.recorder")

        self._lock = threading.Lock()
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._is_recording = False
        self._current_file: str | None = None
        self._running = True

        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()

    def _build_ffmpeg_cmd(self, output_file: str) -> list[str]:
        return [
            "ffmpeg",
            "-y",
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
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            output_file,
        ]

    def _writer_loop(self):
        interval = 1.0 / self._fps
        next_tick = time.time()

        while self._running:
            frame = self._track.get_latest_frame()

            with self._lock:
                proc = self._ffmpeg_proc
                recording = self._is_recording

            if not recording or proc is None or proc.stdin is None:
                time.sleep(0.02)
                next_tick = time.time()
                continue

            if frame is None:
                time.sleep(0.01)
                continue

            if proc.poll() is not None:
                with self._lock:
                    self._is_recording = False
                    self._ffmpeg_proc = None
                self._logger.warning("FFmpeg exited unexpectedly; recording stopped")
                continue

            try:
                proc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                with self._lock:
                    self._is_recording = False
                    self._ffmpeg_proc = None
                self._logger.warning("FFmpeg pipe broken; recording stopped")
                continue
            except Exception as exc:
                with self._lock:
                    self._is_recording = False
                    self._ffmpeg_proc = None
                self._logger.warning("Recording frame write failed: %s", exc)
                continue

            next_tick += interval
            sleep_time = next_tick - time.time()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_tick = time.time()

    def start_recording(self) -> tuple[bool, str]:
        with self._lock:
            if self._is_recording:
                return False, "Already recording"

            try:
                os.makedirs(self._record_dir, exist_ok=True)
            except OSError as exc:
                return False, f"Cannot create output directory: {exc}"

            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self._current_file = os.path.join(self._record_dir, f"recording_{timestamp}.mp4")

            try:
                self._ffmpeg_proc = subprocess.Popen(
                    self._build_ffmpeg_cmd(self._current_file),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                self._current_file = None
                self._ffmpeg_proc = None
                return False, "ffmpeg not found in container"
            except Exception as exc:
                self._current_file = None
                self._ffmpeg_proc = None
                return False, f"Failed to start recorder: {exc}"

            self._is_recording = True
            self._logger.info("Recording started -> %s", self._current_file)
            return True, f"Recording started: {self._current_file}"

    def stop_recording(self) -> tuple[bool, str]:
        with self._lock:
            if not self._is_recording:
                return False, "Not currently recording"

            self._is_recording = False
            proc = self._ffmpeg_proc
            self._ffmpeg_proc = None
            saved_file = self._current_file
            self._current_file = None

        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            except Exception:
                pass

        self._logger.info("Recording stopped -> %s", saved_file)
        return True, f"Saved: {saved_file}"

    def status(self) -> dict:
        with self._lock:
            return {
                "recording": self._is_recording,
                "current_file": self._current_file,
                "record_dir": self._record_dir,
            }

    def close(self):
        self._running = False
        self.stop_recording()
        if self._writer_thread.is_alive():
            self._writer_thread.join(timeout=1)


class GatewayApp:
    def __init__(self):
        self.host = os.getenv("WEB_HOST", "0.0.0.0")
        self.port = int(os.getenv("WEB_PORT", "8080"))
        self.rtmp_url = os.getenv("RTMP_URL", "rtmp://localhost:1935/stream/stream")
        self.width = int(os.getenv("WIDTH", "640"))
        self.height = int(os.getenv("HEIGHT", "360"))
        self.fps = int(os.getenv("FPS", "15"))
        self.record_dir = os.getenv("RECORD_DIR", "/video")

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        self._logger = logging.getLogger("webrtc-gateway")

        self._pcs: set[RTCPeerConnection] = set()
        self._track = RtmpVideoTrack(self.rtmp_url, self.width, self.height, self.fps)
        self._recorder = GatewayRecorder(self._track, self.record_dir, self.width, self.height, self.fps)
        self._ice_servers = self._build_ice_servers()
        self._ice_servers_payload = self._ice_servers_to_payload(self._ice_servers)
        self._relay = MediaRelay()
        self._logger.info("Gateway starting on %s:%d using RTMP source %s", self.host, self.port, self.rtmp_url)

        self.app = web.Application()
        www_dir = Path(__file__).parent / "www"

        async def root_handler(_request):
            return web.FileResponse(www_dir / "index.html")

        self.app.router.add_get("/", root_handler)
        self.app.router.add_static("/", str(www_dir), name="static")
        self.app.router.add_get("/config", self._config)
        self.app.router.add_post("/offer", self._offer)
        self.app.router.add_get("/debug/stream", self._debug_stream)

        # Recording endpoints handled directly in gateway container.
        self.app.router.add_get("/recording/status", self._recording_status)
        self.app.router.add_post("/recording/start", self._recording_start)
        self.app.router.add_post("/recording/stop", self._recording_stop)

    async def _recording_status(self, _request):
        return web.json_response(self._recorder.status())

    async def _recording_start(self, _request):
        success, message = self._recorder.start_recording()
        status = 200 if success else 400
        return web.json_response({"success": success, "message": message}, status=status)

    async def _recording_stop(self, _request):
        success, message = self._recorder.stop_recording()
        status = 200 if success else 400
        return web.json_response({"success": success, "message": message}, status=status)

    async def _debug_stream(self, _request):
        data = self._track.snapshot()
        data["peer_count"] = len(self._pcs)
        return web.json_response(data)

    async def _config(self, _request):
        return web.json_response({"iceServers": self._ice_servers_payload})

    def _build_ice_servers(self) -> list[RTCIceServer]:
        ice_servers: list[RTCIceServer] = []

        stun_server = os.getenv("WEBRTC_STUN_SERVER", "stun:stun.l.google.com:19302").strip()
        if stun_server:
            ice_servers.append(RTCIceServer(urls=stun_server))

        turn_server = os.getenv("WEBRTC_TURN_SERVER", "").strip()
        if turn_server:
            turn_urls = turn_server
            if not turn_urls.startswith(("turn:", "turns:")):
                turn_urls = f"turn:{turn_urls}"

            turn_username = os.getenv("WEBRTC_TURN_USERNAME", "").strip() or None
            turn_credential = os.getenv("WEBRTC_TURN_CREDENTIAL", "").strip() or None
            ice_servers.append(
                RTCIceServer(
                    urls=turn_urls,
                    username=turn_username,
                    credential=turn_credential,
                )
            )

        return ice_servers

    @staticmethod
    def _ice_servers_to_payload(ice_servers: list[RTCIceServer]) -> list[dict]:
        payload: list[dict] = []
        for server in ice_servers:
            item: dict = {"urls": server.urls}
            if server.username:
                item["username"] = server.username
            if server.credential:
                item["credential"] = server.credential
            payload.append(item)
        return payload

    async def _offer(self, request: web.Request):
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
        self._logger.info("Received /offer request")

        pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=self._ice_servers))
        self._pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            self._logger.info("Peer connection state: %s", pc.connectionState)
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await pc.close()
                self._pcs.discard(pc)

        pc.addTrack(self._relay.subscribe(self._track))

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return web.json_response({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
        })

    async def _shutdown(self, _app: web.Application):
        for pc in list(self._pcs):
            await pc.close()
            self._pcs.discard(pc)
        self._recorder.close()
        self._track.close()

    def run(self):
        self.app.on_shutdown.append(self._shutdown)
        web.run_app(self.app, host=self.host, port=self.port)


if __name__ == "__main__":
    GatewayApp().run()
