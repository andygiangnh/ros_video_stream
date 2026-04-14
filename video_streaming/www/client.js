let pc = null;
let statsTimer = null;
let decodedFps = null;
let smoothedFps = null;
let lastFrameNow = null;
let iceServersPromise = null;

async function getIceServers() {
  if (!iceServersPromise) {
    iceServersPromise = fetch('/config')
      .then((response) => {
        if (!response.ok) {
          return { iceServers: [] };
        }

        return response.json();
      })
      .then((data) => data.iceServers || []);
  }

  return iceServersPromise;
}

function updateStatus(text, className = null) {
  const status = document.getElementById('status');
  status.textContent = text;
  status.className = 'status-value';
  if (className) {
    status.classList.add(className);
  }
}

function updateMetrics({ fps = null, rttMs = null, jitterBufferMs = null } = {}) {
  const fmt = (value, suffix = '') => 
    (value == null || Number.isNaN(value)) ? '--' : `${value.toFixed(1)}${suffix}`;
  
  document.getElementById('fps').textContent = fmt(fps);
  document.getElementById('rtt').textContent = fmt(rttMs, ' ms');
  document.getElementById('jitter').textContent = fmt(jitterBufferMs, ' ms');
}

function setButtonState(streaming) {
  document.getElementById('start').disabled = streaming;
  document.getElementById('stop').disabled = !streaming;
}

function startFrameMeter(videoElement) {
  // Use requestVideoFrameCallback for accurate FPS measurement
  if (!videoElement.requestVideoFrameCallback) {
    console.warn('requestVideoFrameCallback not supported, FPS will be unavailable');
    return;
  }

  const onFrame = (now) => {
    if (lastFrameNow !== null && now > lastFrameNow) {
      const instantFps = 1000 / (now - lastFrameNow);
      // Exponential moving average over ~5 frames
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

async function pollStats() {
  if (!pc) {
    return;
  }

  try {
    const stats = await pc.getStats();
    let rttMs = null;
    let jitterBufferMs = null;
    let statsFps = null;

    stats.forEach((report) => {
      // Succeeded candidate pair → RTT
      if (
        report.type === 'candidate-pair' &&
        report.state === 'succeeded' &&
        report.nominated &&
        typeof report.currentRoundTripTime === 'number'
      ) {
        rttMs = report.currentRoundTripTime * 1000;
      }

      // Inbound video RTP → jitter buffer & server-side FPS
      if (report.type === 'inbound-rtp' && report.kind === 'video') {
        if (
          typeof report.jitterBufferDelay === 'number' &&
          typeof report.jitterBufferEmittedCount === 'number' &&
          report.jitterBufferEmittedCount > 0
        ) {
          jitterBufferMs = (report.jitterBufferDelay / report.jitterBufferEmittedCount) * 1000;
        }

        // Use server-reported FPS if available, otherwise decoded FPS
        if (typeof report.framesPerSecond === 'number') {
          statsFps = report.framesPerSecond;
        }
      }
    });

    // Prefer server-reported FPS, fall back to decoded FPS
    updateMetrics({ 
      fps: statsFps ?? decodedFps, 
      rttMs, 
      jitterBufferMs 
    });
  } catch (err) {
    console.error('Error polling stats:', err);
  }
}

async function start() {
  if (pc) {
    return;
  }

  try {
    updateStatus('Starting...', null);
    setButtonState(true);
    updateMetrics();

    // Reset FPS tracking
    decodedFps = null;
    smoothedFps = null;
    lastFrameNow = null;

    // Create peer connection
    const iceServers = await getIceServers();
    pc = new RTCPeerConnection({ iceServers });

    // Request video-only, receive-only transceiver
    pc.addTransceiver('video', { direction: 'recvonly' });

    // Handle incoming media track
    pc.ontrack = (event) => {
      const video = document.getElementById('video');
      video.srcObject = event.streams[0];
      startFrameMeter(video);
    };

    // Create and send SDP offer
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const response = await fetch('/offer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sdp: pc.localDescription.sdp,
        type: pc.localDescription.type,
      }),
    });

    if (!response.ok) {
      throw new Error(`Server error: ${response.status}`);
    }

    const answer = await response.json();

    // Receive and apply SDP answer
    await pc.setRemoteDescription(answer);

    // Start polling WebRTC stats every 1 second
    if (statsTimer) {
      clearInterval(statsTimer);
    }
    statsTimer = setInterval(() => {
      pollStats().catch((err) => console.error('Stats error:', err));
    }, 1000);

    updateStatus('Streaming', 'streaming');
  } catch (err) {
    console.error('Error starting stream:', err);
    updateStatus(`Error: ${err.message}`, 'error');
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

  const video = document.getElementById('video');
  video.srcObject = null;

  updateStatus('Idle', null);
  setButtonState(false);
  updateMetrics();
}

// Attach event listeners
document.getElementById('start').onclick = start;
document.getElementById('stop').onclick = stop;

// Handle page unload
window.addEventListener('beforeunload', () => {
  if (pc) {
    stop();
  }
});
