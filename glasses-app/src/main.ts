/// <reference types="vite/client" />

import { EvenAppBridge, waitForEvenAppBridge, AudioInputSource } from '@evenrealities/even_hub_sdk';

/**
 * Bridge hostname configuration
 *
 * Set via VITE_BRIDGE_HOSTNAME env variable (set in vite.config.ts or .env file)
 * Falls back to hermes.local, then the local IP inferred from hostname.
 */
const BRIDGE_HOSTNAME = import.meta.env.VITE_BRIDGE_HOSTNAME || 'hermes.local';

/** Build WebSocket URL — ws for plain TCP, wss for TLS via Traefik */
const PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = `${PROTOCOL}//${BRIDGE_HOSTNAME}:8765/ws/glasses`;

let ws: WebSocket | null = null;
let bridge: EvenAppBridge | null = null;
let micActive = false;

const $status = document.getElementById('status')!;
const $content = document.getElementById('content')!;
const $mic = document.getElementById('mic')!;

function log(msg: string) {
  console.log('[Hermes]', msg);
}

function setStatus(text: string) {
  $status.textContent = text;
}

function addLine(cls: string, html: string) {
  const div = document.createElement('div');
  div.className = cls;
  div.innerHTML = html;
  $content.appendChild(div);
  $content.scrollTop = $content.scrollHeight;
}

function connect() {
  if (ws) return;
  log('Connecting to ' + WS_URL);
  ws = new WebSocket(WS_URL);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    setStatus('Connected');
    log('WebSocket open');
  };

  ws.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) {
      // PCM audio would not come from server in this design
      return;
    }
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'status') {
        setStatus(`${msg.title}: ${msg.content}`);
      } else if (msg.type === 'text') {
        // Update or append the latest assistant text
        const existing = $content.querySelector('.last-text') as HTMLElement | null;
        if (existing) {
          existing.textContent = msg.content;
        } else {
          const div = document.createElement('div');
          div.className = 'text last-text';
          div.textContent = msg.content;
          $content.appendChild(div);
          $content.scrollTop = $content.scrollHeight;
        }
      }
    } catch (e) {
      log('Bad JSON: ' + ev.data);
    }
  };

  ws.onclose = () => {
    setStatus('Disconnected');
    log('WebSocket closed');
    ws = null;
    // Auto-reconnect after 3s
    setTimeout(connect, 3000);
  };

  ws.onerror = (e) => {
    log('WebSocket error: ' + e);
  };
}

async function toggleMic() {
  if (!bridge || !ws || ws.readyState !== 1) {
    setStatus(ws ? 'Not connected' : 'Bridge not ready');
    return;
  }
  micActive = !micActive;
  $mic.classList.toggle('on', micActive);

  if (micActive) {
    setStatus('Listening...');
    const ok = await bridge.audioControl(true, AudioInputSource.Glasses);
    if (!ok) log('audioControl(true) failed');
  } else {
    setStatus('Tap side to talk');
    const ok = await bridge.audioControl(false);
    if (!ok) log('audioControl(false) failed');
  }
}

async function init() {
  // SDK is a singleton — use waitForEvenAppBridge to ensure it's ready
  bridge = await waitForEvenAppBridge();
  log('Bridge ready: ' + bridge.ready);

  // Listen for evenHubEvent from SDK (includes audio events)
  bridge.onEvenHubEvent((event) => {
    if (event.audioEvent) {
      // PCM16 16kHz mono audio frames from glasses mic
      const pcm = event.audioEvent.audioPcm;
      log('Audio: ' + pcm.length + ' bytes (source: ' + event.audioEvent.source + ')');
      if (ws && ws.readyState === 1) {
        // Convert Uint8Array to ArrayBuffer for WebSocket binary send
        ws.send(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength) as ArrayBuffer);
      }
    }
  });

  connect();
}

init().catch(e => {
  setStatus('Init failed: ' + e.message);
  log('Init error: ' + e);
});
