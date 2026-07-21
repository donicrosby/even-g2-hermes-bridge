/// <reference types="vite/client" />

import {
  waitForEvenAppBridge,
  AudioInputSource,
  OsEventTypeList,
  StartUpPageCreateResult,
  TextContainerProperty,
  TextContainerUpgrade,
  CreateStartUpPageContainer,
} from '@evenrealities/even_hub_sdk';

import type {
  HelloFrame,
  AudioStartFrame,
  AudioStopFrame,
  SimpleInboundFrame,
  SessionsSwitchFrame,
  HelloOkFrame,
  AssistantDeltaFrame,
  AssistantFullFrame,
  ToolStartFrame,
  ToolEndFrame,
  TranscriptFrame,
  ActiveFrame,
  ErrorFrame,
} from './protocol';
import { truncateSessionName } from './lib/session';
import { nextBackoffDelay } from './lib/reconnect';
import { createBridgeQueue } from './lib/bridge';
import {
  serializeState,
  parseState,
  mergeState,
  STATE_KEY,
  type GlassesAppState,
} from './lib/state';

// ===== Configuration =======================================================

function getConfig(): { url: string; token: string } {
  return {
    url: localStorage.getItem('bridge_url') || '',
    token: localStorage.getItem('bridge_token') || '',
  };
}

function isConfigured(): boolean {
  const { url, token } = getConfig();
  return url.length > 0 && token.length > 0;
}

// ===== Bridge-call resilience ==============================================
// Per the `glasses-ui` skill: "Serialize all bridge calls, not just images"
// and "Add a per-call timeout to BLE calls — a single flaky hop can hang ~30s;
// wrap calls in Promise.race with a few-second cap." Every bridge.* call in
// this file goes through `queue.runBridge` to enforce both rules. The queue
// implementation lives in `./lib/bridge.ts` (unit-tested).

const queue = createBridgeQueue();
const runBridge = queue.runBridge;

// ===== Container layout (576×288 canvas) ===================================

const ASSISTANT_CID = 1;
const ASSISTANT_CNAME = 'assistant';
const STATUS_CID = 2;
const STATUS_CNAME = 'status';
const SESSION_CID = 3;
const SESSION_CNAME = 'session';

const ASSISTANT_RECT = { x: 0, y: 0, w: 576, h: 200 };
const STATUS_RECT = { x: 0, y: 200, w: 576, h: 44 };
const SESSION_RECT = { x: 0, y: 244, w: 576, h: 44 };

// ===== Mutable state =======================================================

let bridge: Awaited<ReturnType<typeof waitForEvenAppBridge>> | null = null;
let ws: WebSocket | null = null;
let reconnectAttempts = 0;
let authFailed = false;

let accumulatedAssistantText = '';
let currentSessionId = '';
let currentSessionName = '';
let isCapturing = false;
let backgrounded = false;
let lastTranscript = '';

let unsubscribeEvents: (() => void) | null = null;
let cleanupDone = false;

// ===== State persistence (SDK 0.0.12) ======================================
// SDK 0.0.12 lacks setBackgroundState/onBackgroundRestore, so we persist via
// setLocalStorage/getLocalStorage instead. Restored on init, debounced save
// on each meaningful state change, and flushed on FOREGROUND_EXIT.
// Pure serialize/parse/merge logic lives in lib/state.ts (unit-tested).

async function restoreState(): Promise<void> {
  if (!bridge) return;
  const raw = await runBridge('getLocalStorage', () => bridge!.getLocalStorage(STATE_KEY));
  if (!raw) return;
  try {
    const merged = mergeState(currentMutableState(), parseState(raw));
    accumulatedAssistantText = merged.accumulatedAssistantText;
    currentSessionId = merged.currentSessionId;
    currentSessionName = merged.currentSessionName;
    lastTranscript = merged.lastTranscript;
  } catch (e) {
    console.warn('[Hermes] state restore failed:', e);
  }
}

function currentMutableState(): GlassesAppState {
  return {
    accumulatedAssistantText,
    currentSessionId,
    currentSessionName,
    lastTranscript,
  };
}

let saveTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleSave(): void {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    void saveState();
  }, 500);
}

async function saveState(): Promise<void> {
  if (!bridge) return;
  await runBridge('setLocalStorage', () =>
    bridge!.setLocalStorage(STATE_KEY, serializeState(currentMutableState())),
  );
}

// ===== Rendering ===========================================================

async function upgradeText(cid: number, cname: string, content: string): Promise<void> {
  if (!bridge) return;
  await runBridge('textContainerUpgrade', () =>
    bridge!.textContainerUpgrade(
      new TextContainerUpgrade({
        containerID: cid,
        containerName: cname,
        content,
        contentOffset: 0,
        contentLength: 0,
      }),
    ),
  );
}

function renderAssistant(): void {
  void upgradeText(ASSISTANT_CID, ASSISTANT_CNAME, accumulatedAssistantText || ' ');
}

function setStatus(text: string): void {
  void upgradeText(STATUS_CID, STATUS_CNAME, text);
}

function renderSession(): void {
  const name = currentSessionName || currentSessionId || ' ';
  const truncated = truncateSessionName(name);
  void upgradeText(SESSION_CID, SESSION_CNAME, truncated);
}

// ===== WebSocket ===========================================================

function connect(): void {
  if (ws || authFailed) return;
  if (!isConfigured()) return;

  const { url, token } = getConfig();
  setStatus('Connecting...');

  const socket = new WebSocket(url);
  socket.binaryType = 'arraybuffer';
  ws = socket;

  socket.onopen = () => {
    reconnectAttempts = 0;
    const hello: HelloFrame = {
      t: 'hello',
      token: token,
      device: 'g2',
    };
    socket.send(JSON.stringify(hello));
  };

  socket.onmessage = (ev) => {
    if (ev.data instanceof ArrayBuffer) return;
    try {
      const parsed: unknown = JSON.parse(ev.data as string);
      if (parsed && typeof parsed === 'object') {
        handleFrame(parsed as Record<string, unknown>);
      }
    } catch (e) {
      console.warn('[Hermes] Bad JSON frame:', e);
    }
  };

  socket.onclose = (ev) => {
    ws = null;

    if (ev.code === 1008) {
      authFailed = true;
      setStatus('Auth failed');
      return;
    }

    setStatus('Disconnected');
    scheduleReconnect();
  };

  socket.onerror = () => {
    // onclose will follow; no action needed here.
  };
}

function scheduleReconnect(): void {
  if (authFailed) return;
  const delay = nextBackoffDelay(reconnectAttempts);
  reconnectAttempts += 1;
  setTimeout(connect, delay);
}

function sendFrame(frame: OutboundClientFrame): void {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(frame));
  }
}

// ===== Inbound frame handling ==============================================

type OutboundClientFrame =
  | HelloFrame
  | AudioStartFrame
  | AudioStopFrame
  | SimpleInboundFrame
  | SessionsSwitchFrame;

function handleFrame(frame: Record<string, unknown>): void {
  const t = frame.t as string;
  switch (t) {
    case 'hello.ok':
      handleHelloOk(frame as unknown as HelloOkFrame);
      break;
    case 'assistant.delta':
      handleAssistantDelta(frame as unknown as AssistantDeltaFrame);
      break;
    case 'assistant':
      handleAssistantFull(frame as unknown as AssistantFullFrame);
      break;
    case 'tool.start':
      handleToolStart(frame as unknown as ToolStartFrame);
      break;
    case 'tool.end':
      handleToolEnd(frame as unknown as ToolEndFrame);
      break;
    case 'transcript':
      handleTranscript(frame as unknown as TranscriptFrame);
      break;
    case 'turn.done':
      handleTurnDone();
      break;
    case 'active':
      handleActive(frame as unknown as ActiveFrame);
      break;
    case 'error':
      handleError(frame as unknown as ErrorFrame);
      break;
    default:
      console.warn('[Hermes] Unknown frame type:', t);
  }
}

function handleHelloOk(frame: HelloOkFrame): void {
  setStatus('Connected');
  if (frame.active) {
    currentSessionId = frame.active;
    renderSession();
    scheduleSave();
  }
}

function handleAssistantDelta(frame: AssistantDeltaFrame): void {
  accumulatedAssistantText = frame.text || '';
  renderAssistant();
  void maybeBringToFront();
}

function handleAssistantFull(frame: AssistantFullFrame): void {
  accumulatedAssistantText = frame.text || '';
  renderAssistant();
  scheduleSave();
}

function handleToolStart(frame: ToolStartFrame): void {
  const label = ('label' in frame && frame.label) || frame.name || 'Tool';
  setStatus(label);
}

function handleToolEnd(_frame: ToolEndFrame): void {
  setStatus('');
}

function handleTranscript(frame: TranscriptFrame): void {
  lastTranscript = frame.text || '';
  setStatus(`You said: ${lastTranscript}`);
  scheduleSave();
}

function handleTurnDone(): void {
  accumulatedAssistantText = '';
  scheduleSave();
}

function handleActive(frame: ActiveFrame): void {
  currentSessionId = frame.id;
  currentSessionName = ('name' in frame && frame.name) || frame.id;
  renderSession();
  scheduleSave();
}

function handleError(frame: ErrorFrame): void {
  setStatus(`Error: ${frame.msg || 'unknown'}`);
}

async function maybeBringToFront(): Promise<void> {
  if (!backgrounded || !bridge) return;
  await runBridge('callEvenApp', () => bridge!.callEvenApp('bringToFront'));
}

// ===== Touch handlers =======================================================

async function toggleMic(): Promise<void> {
  if (!bridge) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    setStatus('Not connected');
    return;
  }

  isCapturing = !isCapturing;

  if (isCapturing) {
    setStatus('Listening...');
    const ok = await runBridge('audioControl', () =>
      bridge!.audioControl(true, AudioInputSource.Glasses),
    );
    if (!ok) {
      console.warn('[Hermes] audioControl(true) failed');
      isCapturing = false;
      setStatus('Mic failed');
      return;
    }
    const frame: AudioStartFrame = { t: 'audio.start' };
    sendFrame(frame);
  } else {
    setStatus('Processing...');
    await runBridge('audioControl', () => bridge!.audioControl(false));
    const frame: AudioStopFrame = { t: 'audio.stop' };
    sendFrame(frame);
  }
}

function switchSession(delta: 1 | -1): void {
  const frame: SessionsSwitchFrame = {
    t: 'sessions.switch',
    id: delta > 0 ? '+1' : '-1',
  };
  sendFrame(frame);
}

// ===== Audio streaming =====================================================

function handleAudioPcm(pcm: Uint8Array): void {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const buf = pcm.buffer.slice(
    pcm.byteOffset,
    pcm.byteOffset + pcm.byteLength,
  ) as ArrayBuffer;
  ws.send(buf);
}

// ===== Config screen (phone-side, Even design system) ====================

const EVEN_COLORS = {
  text: '#232323',
  textDim: '#7B7B7B',
  bg: '#FFFFFF',
  surface: '#EEEEEE',
  inputBg: 'rgba(35,35,35,0.08)',
  accent: '#FEF991',
  textOnAccent: '#FFFFFF',
};

function injectPhoneChrome(): void {
  if (document.getElementById('hermes-chrome')) return;
  const chrome = document.createElement('div');
  chrome.id = 'hermes-chrome';
  chrome.style.cssText = [
    'position:fixed', 'bottom:0', 'left:0', 'right:0',
    'padding:12px 20px', `background:${EVEN_COLORS.bg}`,
    `border-top:1px solid ${EVEN_COLORS.surface}`,
    'display:flex', 'justify-content:space-between', 'align-items:center',
    'z-index:9999', 'font-family:system-ui,sans-serif',
  ].join(';');
  chrome.innerHTML = `
    <span style="font-size:13px;color:${EVEN_COLORS.textDim}">Hermes Bridge</span>
    <button id="hermes-settings-btn" style="background:none;border:none;cursor:pointer;
      color:${EVEN_COLORS.text};font-size:14px;font-weight:500;font-family:inherit">
      Settings
    </button>`;
  document.body.appendChild(chrome);
  document.getElementById('hermes-settings-btn')?.addEventListener('click', showConfigScreen);
}

function showConfigScreen(): void {
  const hasExisting = isConfigured();

  if (bridge) {
    void upgradeText(ASSISTANT_CID, ASSISTANT_CNAME,
      hasExisting ? 'Settings opened on phone.' : 'Enter bridge details on your phone.');
  }

  if (ws) {
    ws.close();
    ws = null;
  }

  const existing = getConfig();
  const inputStyle = [
    'width:100%', 'padding:12px 16px', 'font-size:16px',
    `background:${EVEN_COLORS.inputBg}`, `color:${EVEN_COLORS.text}`,
    'border:none', 'border-radius:8px',
    'box-sizing:border-box', 'margin:0 0 16px',
    'font-family:inherit',
  ].join(';');

  const btnBase = [
    'padding:12px 24px', 'font-size:16px', 'font-weight:600',
    'border:none', 'border-radius:8px', 'cursor:pointer', 'font-family:inherit',
  ].join(';');

  const form = document.createElement('div');
  form.style.cssText = [
    'position:fixed', 'top:0', 'left:0', 'right:0', 'bottom:0',
    `background:${EVEN_COLORS.bg}`, 'padding:20px',
    'overflow-y:auto', 'z-index:10000', 'font-family:system-ui,sans-serif',
  ].join(';');
  form.innerHTML = `
    <div style="max-width:420px;margin:0 auto;padding-top:24px">
      <h2 style="margin:0 0 4px;font-size:24px;font-weight:600;color:${EVEN_COLORS.text};letter-spacing:-0.02em">
        ${hasExisting ? 'Bridge Settings' : 'Bridge Setup'}
      </h2>
      <p style="margin:0 0 24px;font-size:16px;color:${EVEN_COLORS.textDim}">
        ${hasExisting ? 'Edit your bridge connection.' : 'Enter your bridge server details.'}
      </p>
      <label style="display:block;font-size:11px;font-weight:500;text-transform:uppercase;
        letter-spacing:0.04em;color:${EVEN_COLORS.textDim};margin:0 0 4px">Bridge URL</label>
      <input id="hermes-url" type="text" value="${existing.url}" placeholder="wss://your-host:8443" style="${inputStyle}" />
      <label style="display:block;font-size:11px;font-weight:500;text-transform:uppercase;
        letter-spacing:0.04em;color:${EVEN_COLORS.textDim};margin:0 0 4px">Token</label>
      <input id="hermes-token" type="password" value="${existing.token}" placeholder="bridge token" style="${inputStyle}" />
      <div style="display:flex;gap:8px;margin-top:8px">
        <button id="hermes-save-btn" style="${btnBase};background:${EVEN_COLORS.accent};color:${EVEN_COLORS.textOnAccent}">
          ${hasExisting ? 'Save & Reconnect' : 'Connect'}
        </button>
        ${hasExisting ? `<button id="hermes-cancel-btn" style="${btnBase};background:${EVEN_COLORS.surface};color:${EVEN_COLORS.text}">Cancel</button>` : ''}
      </div>
      <p id="hermes-error" style="color:#c33;margin-top:12px;font-size:14px;display:none"></p>
    </div>`;
  document.body.appendChild(form);

  const saveBtn = document.getElementById('hermes-save-btn');
  const cancelBtn = document.getElementById('hermes-cancel-btn');
  const errEl = document.getElementById('hermes-error');

  function showErr(msg: string): void {
    if (errEl) {
      errEl.textContent = msg;
      errEl.style.display = 'block';
    }
  }

  saveBtn?.addEventListener('click', () => {
    const urlInput = document.getElementById('hermes-url') as HTMLInputElement | null;
    const tokenInput = document.getElementById('hermes-token') as HTMLInputElement | null;
    const urlVal = urlInput?.value?.trim() || '';
    const tokenVal = tokenInput?.value?.trim() || '';

    if (!urlVal || !tokenVal) {
      showErr('Both URL and token are required.');
      return;
    }
    if (!urlVal.startsWith('ws://') && !urlVal.startsWith('wss://')) {
      showErr('URL must start with ws:// or wss://');
      return;
    }

    localStorage.setItem('bridge_url', urlVal);
    localStorage.setItem('bridge_token', tokenVal);
    location.reload();
  });

  cancelBtn?.addEventListener('click', () => {
    form.remove();
  });
}

// ===== Page bootstrap ======================================================

async function buildPage(): Promise<void> {
  if (!bridge) return;
  const result = await runBridge('createStartUpPageContainer', () =>
    bridge!.createStartUpPageContainer(
      new CreateStartUpPageContainer({
        containerTotalNum: 3,
        textObject: [
          new TextContainerProperty({
            xPosition: ASSISTANT_RECT.x,
            yPosition: ASSISTANT_RECT.y,
            width: ASSISTANT_RECT.w,
            height: ASSISTANT_RECT.h,
            containerID: ASSISTANT_CID,
            containerName: ASSISTANT_CNAME,
            isEventCapture: 1,
            borderWidth: 0,
            borderColor: 0,
            paddingLength: 4,
            content: accumulatedAssistantText || ' ',
          }),
          new TextContainerProperty({
            xPosition: STATUS_RECT.x,
            yPosition: STATUS_RECT.y,
            width: STATUS_RECT.w,
            height: STATUS_RECT.h,
            containerID: STATUS_CID,
            containerName: STATUS_CNAME,
            isEventCapture: 0,
            borderWidth: 0,
            borderColor: 0,
            paddingLength: 4,
            content: 'Connecting...',
          }),
          new TextContainerProperty({
            xPosition: SESSION_RECT.x,
            yPosition: SESSION_RECT.y,
            width: SESSION_RECT.w,
            height: SESSION_RECT.h,
            containerID: SESSION_CID,
            containerName: SESSION_CNAME,
            isEventCapture: 0,
            borderWidth: 0,
            borderColor: 0,
            paddingLength: 4,
            content: currentSessionName || ' ',
          }),
        ],
      }),
    ),
  );
  if (result !== StartUpPageCreateResult.success) {
    console.error('[Hermes] createStartUpPageContainer failed:', result);
  }
}

function registerEventHandler(): void {
  if (!bridge) return;
  unsubscribeEvents = bridge.onEvenHubEvent((event) => {
    if (event.audioEvent) {
      handleAudioPcm(event.audioEvent.audioPcm);
      return;
    }

    if (event.textEvent) {
      const type = event.textEvent.eventType ?? OsEventTypeList.CLICK_EVENT;
      if (type === OsEventTypeList.SCROLL_TOP_EVENT) switchSession(-1);
      else if (type === OsEventTypeList.SCROLL_BOTTOM_EVENT) switchSession(1);
      return;
    }

    if (event.sysEvent) {
      const type = event.sysEvent.eventType ?? OsEventTypeList.CLICK_EVENT;
      switch (type) {
        case OsEventTypeList.CLICK_EVENT:
          void toggleMic();
          break;
        case OsEventTypeList.DOUBLE_CLICK_EVENT:
          // Per the `handle-input` skill: call `shutDownPageContainer(1)` to
          // show the system exit dialog. Do NOT clean up resources here — the
          // user can still cancel. If they confirm, the SDK fires
          // SYSTEM_EXIT_EVENT (7) and cleanup runs in cleanupAndExit() below.
          if (bridge) {
            void runBridge('shutDownPageContainer', () => bridge!.shutDownPageContainer(1));
          }
          break;
        case OsEventTypeList.FOREGROUND_ENTER_EVENT:
          backgrounded = false;
          break;
        case OsEventTypeList.FOREGROUND_EXIT_EVENT:
          backgrounded = true;
          void saveState();
          break;
        case OsEventTypeList.ABNORMAL_EXIT_EVENT:
        case OsEventTypeList.SYSTEM_EXIT_EVENT:
          cleanupAndExit();
          break;
      }
    }
  });
}

async function init(): Promise<void> {
  bridge = await waitForEvenAppBridge();
  console.warn('[Hermes] Bridge ready');

  if (!isConfigured()) {
    await buildPage();
    showConfigScreen();
    return;
  }

  await restoreState();
  await buildPage();
  injectPhoneChrome();
  registerEventHandler();
  connect();
}

function cleanupAndExit(): void {
  if (cleanupDone) return;
  cleanupDone = true;

  if (unsubscribeEvents) {
    try {
      unsubscribeEvents();
    } catch (e) {
      console.warn('[Hermes] unsubscribe failed:', e);
    }
    unsubscribeEvents = null;
  }

  if (isCapturing && bridge) {
    void runBridge('audioControl', () => bridge!.audioControl(false));
  }
  if (ws) {
    ws.close();
    ws = null;
  }
  void saveState();
}

init().catch((e) => {
  console.error('[Hermes] Init failed:', e);
});
