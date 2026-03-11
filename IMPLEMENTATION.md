# ROS 2 WebRTC Camera Streaming - Implementation Guide

## Overview

This project implements a **multi-client, low-latency camera streaming system** using ROS 2 and WebRTC. It captures video from a Linux V4L2 camera device via OpenCV, encodes it in real-time, and streams it to multiple web browsers (desktop and mobile) simultaneously using WebRTC peer connections.

### Key Features
- **Multi-client support**: Multiple browsers can view the same camera feed concurrently
- **Low-latency streaming**: Real-time video with configurable resolution/FPS
- **Browser UI**: No plugins required; runs on any modern WebRTC-capable browser
- **Performance metrics**: On-page FPS/RTT/jitter buffer indicators for objective tuning
- **Docker containerized**: Fully isolated ROS 2 + OpenCV + WebRTC stack with single container command

---

## Architecture

### Component Diagram

```
┌─────────────────────────────────────────────────────┐
│  Docker Container (ROS 2 Humble)                    │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  WebRTC Camera Node (Python)                 │   │
│  │                                              │   │
│  │  OpenCV Camera Reader (Background Thread)    │   │
│  │    ↓                                         │   │
│  │    Latest Frame Buffer (Lock-Protected)     │   │
│  │    ↓                                         │   │
│  │  OpenCVCameraTrack (aiortc VideoStreamTrack)│   │
│  │    ↓                                         │   │
│  │  MediaRelay (Fan-Out to All Peers)          │   │
│  │    ↓                                         │   │
│  │  RTCPeerConnection (Per Client)             │   │
│  │    ↓                                         │   │
│  │  aiohttp Web Server (Signaling)             │   │
│  │    - GET  /           → HTML UI             │   │
│  │    - GET  /client.js  → WebRTC JS client    │   │
│  │    - POST /offer      → SDP negotiation     │   │
│  └──────────────────────────────────────────────┘   │
│                             ↑                        │
│                        (Port 8080)                   │
└─────────────────────────────────────────────────────┘
                         Network (Host or Bridge)
                                 ↓
           ┌─────────────────────┴─────────────────────┐
           ↓                                           ↓
    ┌─────────────┐                         ┌─────────────────┐
    │ PC Browser  │                         │ Mobile Browser  │
    │             │                         │                 │
    │ • WebRTC JS │                         │ • WebRTC JS     │
    │ • <video>   │                         │ • <video>       │
    │ • Metrics   │                         │ • Metrics       │
    └─────────────┘                         └─────────────────┘
```

### Data Flow

1. **Camera Capture** (`_reader_loop` thread):
   - Runs in background, continuously reads frames from `/dev/video0`
   - Sets buffer size to 1 to avoid frame accumulation (latency reduction)
   - Stores only the **latest frame** in a thread-safe buffer
   - Old frames are discarded to maintain low latency

2. **Encoding & Streaming** (`OpenCVCameraTrack.recv()`):
   - Async generator that yields video frames to WebRTC layer
   - Converts OpenCV BGR→RGB format for WebRTC compatibility
   - Uses `MediaRelay` to clone the same frame to multiple peers simultaneously
   - Each peer receives its own `RTCTrack` subscription

3. **Signaling** (`_offer` handler):
   - Browser sends WebRTC SDP offer with `createOffer()`
   - Server creates a new `RTCPeerConnection` per browser
   - Server adds media track via relay and sends back SDP answer
   - Both sides complete ICE negotiation

4. **Browser Rendering** (client JS):
   - `ontrack` callback receives the video stream
   - Attaches stream to `<video>` element
   - `requestVideoFrameCallback()` measures decoded FPS
   - `pc.getStats()` polls RTT and jitter buffer metrics every 1 second

---

## Implementation Details

### Server-Side (Python)

#### 1. OpenCV Camera Reader (Low-Latency Thread)

**File**: `video_streaming/webrtc_camera_node.py`

```python
class OpenCVCameraTrack(VideoStreamTrack):
    def __init__(self, camera_index, width, height, fps):
        self._capture = cv2.VideoCapture(camera_index)
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimal buffer
        self._capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # Hardware encoding hint
        
        self._latest_frame = None
        self._frame_lock = threading.Lock()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
    
    def _reader_loop(self):
        """Background thread: continuously read and store the latest frame."""
        while self._running:
            ok, frame_bgr = self._capture.read()
            if not ok:
                time.sleep(0.01)
                continue
            
            # Resize if needed
            if frame_bgr.shape != (self._height, self._width):
                frame_bgr = cv2.resize(frame_bgr, (self._width, self._height))
            
            # Store as latest (overwrite previous frame)
            with self._frame_lock:
                self._latest_frame = frame_bgr
    
    async def recv(self):
        """Async generator: fetch latest frame and convert for WebRTC."""
        pts, time_base = await self.next_timestamp()
        
        # Wait up to 100ms (20 * 5ms) for a fresh frame
        frame_bgr = None
        for _ in range(20):
            with self._frame_lock:
                if self._latest_frame is not None:
                    frame_bgr = self._latest_frame.copy()
                    break
            await asyncio.sleep(0.005)
        
        if frame_bgr is None:
            raise RuntimeError("Camera frame read failed")
        
        # Convert BGR→RGB and wrap in av.VideoFrame for WebRTC
        frame = VideoFrame.from_ndarray(frame_bgr, format="bgr24")
        frame.pts = pts
        frame.time_base = time_base
        return frame
```

**Key Design Decisions**:
- **Background reader thread**: Decouples camera I/O from async WebRTC layer
- **Single latest-frame buffer**: Prevents frame accumulation that causes lag
- **Lock-protected access**: Thread-safe reads/writes
- **MJPG hint**: Tells camera to use hardware JPEG if available (reduces CPU)

#### 2. Multi-Client Support (MediaRelay)

```python
class WebRTCCameraNode(Node):
    def __init__(self, host, port, camera_index, width, height, fps):
        # Single shared camera track
        self._camera_track = OpenCVCameraTrack(
            camera_index=camera_index,
            width=width,
            height=height,
            fps=fps,
        )
        
        # MediaRelay: fans out one track to many RTCPeerConnections
        self._relay = MediaRelay()
        
        ...
    
    async def _offer(self, request):
        """Handle peer SDP offer and create subscription."""
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
        
        # Create new peer connection
        pc = RTCPeerConnection()
        self._pcs.add(pc)
        
        # Subscribe this peer to the shared camera via relay
        pc.addTrack(self._relay.subscribe(self._camera_track))
        
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        
        return web.json_response({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
        })
```

**Why MediaRelay**:
- `aiortc.contrib.media.MediaRelay` is a specialized class that clones a single track to multiple destinations
- Each subscriber gets the same frames at approximately the same timestamp
- Avoids creating duplicate `VideoCapture` handles (which would conflict on single-camera devices)
- Minimal memory overhead per new peer

#### 3. Async Event Loop Threading

```python
def __init__(self, ...):
    self._loop = asyncio.new_event_loop()
    self._loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
    self._loop_thread.start()
    
    # Run async setup on the dedicated loop
    startup = asyncio.run_coroutine_threadsafe(self._start_server(), self._loop)
    startup.result(timeout=10)

def _run_event_loop(self):
    """Run aiohttp/aiortc event loop forever in background thread."""
    asyncio.set_event_loop(self._loop)
    self._loop.run_forever()
```

**Why separate thread**:
- `rclpy.spin()` is a blocking call (ROS 2 event loop)
- WebRTC signaling needs its own asyncio loop
- Running both in separate threads prevents hang-up

### Client-Side (JavaScript/HTML)

#### 1. WebRTC Peer Setup

```javascript
async function start() {
    document.getElementById('status').textContent = 'Starting...';
    
    // Create peer connection
    pc = new RTCPeerConnection();
    
    // Request video-only track (recvonly = viewer mode)
    pc.addTransceiver('video', { direction: 'recvonly' });
    
    // Handle incoming media track
    pc.ontrack = (event) => {
        const video = document.getElementById('video');
        video.srcObject = event.streams[0];
        startFrameMeter(video);
    };
    
    // Create SDP offer and send to server
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    
    const response = await fetch('/offer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            sdp: pc.localDescription.sdp,
            type: pc.localDescription.type
        }),
    });
    
    // Receive and apply SDP answer from server
    const answer = await response.json();
    await pc.setRemoteDescription(answer);
    
    // Start stats polling for metrics
    statsTimer = setInterval(() => pollStats().catch(() => {}), 1000);
}
```

#### 2. Decoded FPS Measurement

```javascript
function startFrameMeter(videoElement) {
    // requestVideoFrameCallback: browser callback on every decoded frame
    const onFrame = (now) => {
        if (lastFrameNow !== null && now > lastFrameNow) {
            const instantFps = 1000 / (now - lastFrameNow);
            // Smooth over ~5 frames (exponential moving average)
            smoothedFps = smoothedFps === null 
                ? instantFps 
                : (smoothedFps * 0.8 + instantFps * 0.2);
            decodedFps = smoothedFps;
        }
        lastFrameNow = now;
        
        if (pc) {
            videoElement.requestVideoFrameCallback(onFrame);
        }
    };
    
    videoElement.requestVideoFrameCallback(onFrame);
}
```

**Advantages over rate calculation**:
- `requestVideoFrameCallback()` is called by the browser immediately after rendering a frame
- Avoids timing error from `setInterval()` or `setTimeout()`
- Directly measures browser decode/render performance, not network

#### 3. WebRTC Stats Collection

```javascript
async function pollStats() {
    if (!pc) return;
    
    const stats = await pc.getStats();
    let rttMs = null, jitterBufferMs = null, statsFps = null;
    
    stats.forEach((report) => {
        // Succeeded candidate pair → RTT
        if (
            report.type === 'candidate-pair' &&
            report.state === 'succeeded' &&
            report.nominated
        ) {
            rttMs = report.currentRoundTripTime * 1000;
        }
        
        // Inbound RTP video → jitter buffer & decoded FPS
        if (report.type === 'inbound-rtp' && report.kind === 'video') {
            if (report.jitterBufferEmittedCount > 0) {
                jitterBufferMs = 
                    (report.jitterBufferDelay / report.jitterBufferEmittedCount) * 1000;
            }
            if (typeof report.framesPerSecond === 'number') {
                statsFps = report.framesPerSecond;
            }
        }
    });
    
    setMetrics({ 
        fps: statsFps ?? decodedFps, 
        rttMs, 
        jitterBufferMs 
    });
}

function setMetrics({ fps = null, rttMs = null, jitterBufferMs = null } = {}) {
    document.getElementById('metrics').textContent =
        `FPS: ${fps?.toFixed(1) ?? '--'} | RTT: ${rttMs?.toFixed(1) ?? '--'} ms | Jitter Buffer: ${jitterBufferMs?.toFixed(1) ?? '--'} ms`;
}
```

**Metrics Interpretation**:
- **FPS**: Video decoding rate at browser
- **RTT (Round-Trip Time)**: Network delay between client and server
- **Jitter Buffer**: Decoder's internal buffer depth; if growing, network/server can't keep up

---

## Dockerfile & Container Setup

**File**: `docker/Dockerfile`

```dockerfile
FROM ros:humble-ros-base

# 1. Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-colcon-common-extensions \
    python3-opencv \
    python3-pip \
    ros-humble-cv-bridge \
    ros-humble-sensor-msgs \
    ros-humble-image-transport \
    v4l-utils \
    && rm -rf /var/lib/apt/lists/*

# 2. Install Python WebRTC/asyncio packages
RUN pip3 install --no-cache-dir aiortc aiohttp av

# 3. Copy and build ROS 2 package
WORKDIR /ws/src/video_streaming
COPY . /ws/src/video_streaming

WORKDIR /ws
RUN source /opt/ros/humble/setup.bash \
    && colcon build --packages-select video_streaming

# 4. Setup entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "run", "video_streaming", "webrtc_camera_node"]
```

**Entrypoint** (`docker/entrypoint.sh`):

```bash
#!/usr/bin/env bash
set -e

source /opt/ros/humble/setup.bash
source /ws/install/setup.bash

exec "$@"
```

This ensures:
1. ROS 2 environment is sourced
2. Generated package setup is sourced
3. Command can run immediately

---

## ROS 2 Package Structure

**File**: `package.xml`

```xml
<?xml version="1.0"?>
<package format="3">
  <name>video_streaming</name>
  <version>0.0.0</version>
  <description>ROS 2 WebRTC camera streaming node</description>
  <maintainer email="...">...</maintainer>
  <license>...</license>

  <!-- ROS 2 dependencies -->
  <depend>rclpy</depend>
  <depend>sensor_msgs</depend>
  <depend>cv_bridge</depend>

  <!-- Test dependencies -->
  <test_depend>ament_copyright</test_depend>
  <test_depend>ament_flake8</test_depend>
  <test_depend>ament_pep257</test_depend>
  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
```

**File**: `setup.py`

```python
from setuptools import find_packages, setup

setup(
    name='video_streaming',
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[...],
    install_requires=['setuptools'],
    entry_points={
        'console_scripts': [
            'webrtc_camera_node = video_streaming.webrtc_camera_node:main',
        ],
    },
)
```

The entry point wires the `main()` function as a ROS 2 console script, allowing:
```bash
ros2 run video_streaming webrtc_camera_node --host 0.0.0.0 --port 8080
```

---

## Latency Optimizations

### 1. **Camera Buffer Minimization**
```python
self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
```
Tells OpenCV to keep only 1 frame in the capture buffer (instead of default 30), so new frames reach the application quicker.

### 2. **Latest-Frame-Only Pattern**
The background reader thread **overwrites** the previous frame, never queues. This prevents a slow consumer from accumulating old frames.

### 3. **No Encoding Delay**
WebRTC/aiortc uses hardware-accelerated or OS-provided codecs (VP9, H.264). No manual re-encoding step.

### 4. **Jitter Buffer Management**
Browser's WebRTC stack has built-in jitter buffer to smooth out variable network delays. Keep it **low** by maintaining consistent bitrate:
- Lower resolution or FPS if jitter buffer grows
- Example: `640x360@12fps` instead of `1920x1080@30fps`

### 5. **Configurable Defaults**
```bash
WIDTH=426 HEIGHT=240 FPS=10 ./docker/run_container.sh
```
Allows tuning without code changes for different network conditions.

---

## Scaling & Limitations

### Current Setup
- **Single camera device** → Multiple browsers (1-to-many)
- **Tested concurrency**: 2+ simultaneous viewers
- **Typical latency**: 200-500ms depending on network

### Scaling Bottlenecks
1. **CPU**: Each new RTCPeerConnection adds codec overhead. Measure with `docker stats video_streaming_webrtc`
2. **Network**: Server's uplink bandwidth = sum of all peer bitrates. For `640x360@12fps`, ~500kbps per peer is typical
3. **Camera**: Single camera input; if frozen/slow, all peers see the same delay

### Improving Scalability
- Use **multiple containers** with different source cameras and scale horizontally
- Add **TURN/STUN servers** for mobile/NAT traversal
- Implement **adaptive bitrate** (not currently done; would require custom client negotiation)

---

## Troubleshooting

### 1. Mobile cannot see stream (page loads but blank video)
- **Cause**: ICE candidate gathering failed or no network path
- **Fix**: Enable `USE_HOST_NETWORK=1` (default now) so peers use your PC's real IP
- **Verify**: `docker logs video_streaming_webrtc` should show "WebRTC signaling server started"

### 2. Stream very laggy on mobile
- **Cause**: Mobile has lower bandwidth; server res/fps too high
- **Fix**: Reduce: `WIDTH=426 HEIGHT=240 FPS=10 ./docker/run_container.sh`
- **Monitor**: Watch "Jitter Buffer" on metrics; if > 500ms, reduce further

### 3. Only one client can view
- **Cause**: Old code created unique camera reader per peer (would fail on 2nd peer)
- **Fix**: We use `MediaRelay` to share one camera track (implemented)

### 4. Page takes 5+ seconds to load
- **Cause**: Server responding slowly to HTTP or camera is blocking initialization
- **Fix**: Check `docker logs` for errors; ensure `/dev/video0` exists and is readable

### 5. Docker build fails
- **Common cause**: Missing v4l-utils or permission to mount camera device
- **Fix**: On host, run `ls -l /dev/video0` to confirm device exists and is readable

---

## Summary of Design Patterns

| Pattern | Purpose | Implementation |
|---------|---------|-----------------|
| Background reader thread | Decouple I/O from async loop | `threading.Thread(_reader_loop)` |
| Single latest-frame buffer | Minimize latency/buffering | Overwrite, don't queue |
| MediaRelay | Multi-client from single source | `aiortc.contrib.media.MediaRelay` |
| Separate async loop | Don't block ROS 2 spin | `asyncio.new_event_loop()` in daemon thread |
| requestVideoFrameCallback | Accurate FPS measurement | Browser-native timing, no polling |
| WebRTC getStats() | Network/buffer monitoring | Per-second polling of connection stats |
| Configurable resolution/FPS | Adaptive tuning | Environment variables in run script |

---

## Next Steps / Future Work

1. **TURN/STUN Server**: Add public STUN config for mobile outside home network
2. **Adaptive Bitrate**: Monitor jitter buffer, auto-lower quality if buffer grows
3. **ROS 2 Integration**: Publish camera frames as `sensor_msgs/Image` in addition to WebRTC
4. **Recording**: Store WebRTC stream to disk for later playback
5. **Multi-camera**: Load-balance multiple cameras across multiple nodes
6. **Security**: Add authentication (OAuth/JWT) before allowing stream access
