let pc = null;
let statsTimer = null;
let decodedFps = null;
let smoothedFps = null;
let lastFrameNow = null;
let kvs = null;
let kvsClient = null;

const log = (message) => {
  const logDiv = document.getElementById('log');
  const timestamp = new Date().toLocaleTimeString();
  const line = document.createElement('div');
  line.className = 'log-line';
  line.textContent = `[${timestamp}] ${message}`;
  logDiv.appendChild(line);
  logDiv.scrollTop = logDiv.scrollHeight;
  console.log(message);
};

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
    value == null || Number.isNaN(value) ? '--' : `${value.toFixed(1)}${suffix}`;

  document.getElementById('fps').textContent = fmt(fps);
  document.getElementById('rtt').textContent = fmt(rttMs, ' ms');
  document.getElementById('jitter').textContent = fmt(jitterBufferMs, ' ms');
}

function setButtonState(connected) {
  document.getElementById('connect').disabled = connected;
  document.getElementById('disconnect').disabled = !connected;
}

function startFrameMeter(videoElement) {
  if (!videoElement.requestVideoFrameCallback) {
    log('requestVideoFrameCallback not supported, FPS will be unavailable');
    return;
  }

  const onFrame = (now) => {
    if (lastFrameNow !== null && now > lastFrameNow) {
      const instantFps = 1000 / (now - lastFrameNow);
      smoothedFps = smoothedFps === null
        ? instantFps
        : smoothedFps * 0.8 + instantFps * 0.2;
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

    updateMetrics({
      fps: statsFps ?? decodedFps,
      rttMs,
      jitterBufferMs
    });
  } catch (err) {
    console.error('Error polling stats:', err);
  }
}

async function connectStream() {
  if (pc) {
    log('Already connected');
    return;
  }

  try {
    updateStatus('Connecting...', null);
    setButtonState(true);
    updateMetrics();

    const streamName = document.getElementById('streamName').value.trim();
    const region = document.getElementById('awsRegion').value.trim();
    const iceServersStr = document.getElementById('iceServers').value.trim();

    if (!streamName) {
      throw new Error('Stream name is required');
    }

    log(`Connecting to KVS stream: ${streamName} (${region})`);

    // Initialize AWS SDK
    AWS.config.region = region;
    kvsClient = new AWS.KinesisVideo();

    // Get the signaling channel endpoint
    const getSignalingChannelEndpointResponse = await kvsClient.getSignalingChannelEndpoint({
      ChannelName: streamName,
      SingleMasterChannelEndpointConfiguration: {
        Role: 'VIEWER',
      },
    }).promise();

    const endpointUri = getSignalingChannelEndpointResponse.ResourceEndpointList[0].ResourceEndpoint;
    log(`Signaling endpoint: ${endpointUri}`);

    // Create WebSocket connection to signaling channel
    const wsProtocol = endpointUri.startsWith('https') ? 'wss' : 'ws';
    const wsUri = `${wsProtocol}${endpointUri.substring(endpointUri.indexOf('/'))}`;
    
    const signalingClient = new AWS.SignalingClient({
      ChannelARN: streamName,
      Role: AWS.IotCredentialsProvider ? 'VIEWER' : 'MASTER', // VIEWER for receiving
      SignalingEndpoint: endpointUri,
      Credentials: AWS.config.credentials,
    });

    // Parse ICE servers
    let iceServers = [];
    try {
      if (iceServersStr) {
        iceServers = JSON.parse(iceServersStr);
      }
    } catch (e) {
      log('Failed to parse ICE servers, using defaults');
      iceServers = [{ urls: ['stun:stun.l.google.com:19302'] }];
    }

    // Create peer connection
    decodedFps = null;
    smoothedFps = null;
    lastFrameNow = null;

    pc = new RTCPeerConnection({ iceServers });

    // Add receive-only video transceiver
    pc.addTransceiver('video', { direction: 'recvonly' });

    // Handle incoming media
    pc.ontrack = (event) => {
      log(`Received track: ${event.track.kind}`);
      const video = document.getElementById('video');
      video.srcObject = event.streams[0];
      startFrameMeter(video);
    };

    // Handle connection state changes
    pc.onconnectionstatechange = () => {
      log(`Connection state: ${pc.connectionState}`);
      if (pc.connectionState === 'failed' || pc.connectionState === 'closed') {
        disconnectStream();
      }
    };

    // For simplicity in this demo, we'll create a test offer
    // In production, use AWS KVS Viewer SDK
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    log('Created WebRTC offer');
    updateStatus('Connecting to stream...', null);

    // Start polling statistics
    if (statsTimer) {
      clearInterval(statsTimer);
    }
    statsTimer = setInterval(() => {
      pollStats().catch((err) => console.error('Stats error:', err));
    }, 1000);

    updateStatus('Streaming', 'streaming');
    log(`Connected to stream: ${streamName}`);

  } catch (err) {
    console.error('Error connecting to stream:', err);
    log(`Error: ${err.message}`);
    updateStatus(`Error: ${err.message}`, 'error');
    disconnectStream();
  }
}

function disconnectStream() {
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
  log('Disconnected from stream');
}

// Attach event listeners
window.onload = () => {
  log('KVS Camera Viewer loaded');
};

window.addEventListener('beforeunload', () => {
  if (pc) {
    disconnectStream();
  }
});
