/// <reference types="vite/client" />

import { EvenAppBridge } from '@evenrealities/even_hub_sdk';

const WS_URL = import.meta.env.VITE_BRIDGE_URL || 'wss://hermes.local/ws/glasses';

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

function toggleMic() {
  if (!bridge) return;
  if (!ws || ws.readyState !== 1) {
    setStatus('Not connected');
    return;
  }
  micActive = !micActive;
  $mic.classList.toggle('on', micActive);

  if (micActive) {
    setStatus('Listening...');
    bridge.audioControl(true);
  } else {
    setStatus('Tap side to talk');
    bridge.audioControl(false);
  }
}

async function init() {
  bridge = new EvenAppBridge();
  await bridge.init();

  bridge.onEvent((event: any) => {
    if (event.type === 'audio') {
      // PCM16 16kHz mono audio frames from glasses mic
      if (ws && ws.readyState === 1) {
        ws.send(event.data);
      }
    }
  });

  // Side-touch toggles mic
  bridge.onSideTouch((side: string) => {
    log('Side touch: ' + side);
    toggleMic();
  });

  connect();
}

init().catch(e => {
  setStatus('Init failed: ' + e.message);
  log('Init error: ' + e);
});
