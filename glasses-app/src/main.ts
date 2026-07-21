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
import {
  serializeState,
  parseState,
  mergeState,
  STATE_KEY,
  type GlassesAppState,
} from './lib/state';

// ===== Configuration =======================================================

const BRIDGE_URL =
  localStorage.getItem('bridge_url') ||
  import.meta.env.VITE_BRIDGE_URL ||
  'wss://hermes.your-tailnet.ts.net:8443';

const BRIDGE_TOKEN =
  localStorage.getItem('bridge_token') ||
  import.meta.env.VITE_BRIDGE_TOKEN ||
  '';

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

// ===== State persistence (SDK 0.0.12) ======================================
// SDK 0.0.12 lacks setBackgroundState/onBackgroundRestore, so we persist via
// setLocalStorage/getLocalStorage instead. Restored on init, debounced save
// on each meaningful state change, and flushed on FOREGROUND_EXIT.
// Pure serialize/parse/merge logic lives in lib/state.ts (unit-tested).

async function restoreState(): Promise<void> {
  if (!bridge) return;
  try {
    const raw = await bridge.getLocalStorage(STATE_KEY);
    if (!raw) return;
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
  try {
    await bridge.setLocalStorage(STATE_KEY, serializeState(currentMutableState()));
  } catch (e) {
    console.warn('[Hermes] state save failed:', e);
  }
}

// ===== Rendering ===========================================================

async function upgradeText(cid: number, cname: string, content: string): Promise<void> {
  if (!bridge) return;
  try {
    await bridge.textContainerUpgrade(
      new TextContainerUpgrade({
        containerID: cid,
        containerName: cname,
        content,
        contentOffset: 0,
        contentLength: 0,
      }),
    );
  } catch (e) {
    console.warn('[Hermes] textContainerUpgrade failed:', e);
  }
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

  setStatus('Connecting...');

  const socket = new WebSocket(BRIDGE_URL);
  socket.binaryType = 'arraybuffer';
  ws = socket;

  socket.onopen = () => {
    reconnectAttempts = 0;
    const hello: HelloFrame = {
      t: 'hello',
      token: BRIDGE_TOKEN,
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
  try {
    await bridge.callEvenApp('bringToFront');
  } catch {
    // Silent — headless WebView still renders the frame.
  }
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
    const ok = await bridge.audioControl(true, AudioInputSource.Glasses);
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
    await bridge.audioControl(false);
    const frame: AudioStopFrame = { t: 'audio.stop' };
    sendFrame(frame);
  }
}

function interruptAgent(): void {
  setStatus('Stopped');
  sendFrame({ t: 'stop' } satisfies SimpleInboundFrame);
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

// ===== Page bootstrap ======================================================

async function buildPage(): Promise<void> {
  if (!bridge) return;
  const result = await bridge.createStartUpPageContainer(
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
          borderWidth: 1,
          borderColor: 8,
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
  );
  if (result !== StartUpPageCreateResult.success) {
    console.error('[Hermes] createStartUpPageContainer failed:', result);
  }
}

function registerEventHandler(): void {
  if (!bridge) return;
  bridge.onEvenHubEvent((event) => {
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
          interruptAgent();
          break;
        case OsEventTypeList.FOREGROUND_ENTER_EVENT:
          backgrounded = false;
          break;
        case OsEventTypeList.FOREGROUND_EXIT_EVENT:
          backgrounded = true;
          void saveState();
          break;
      }
    }
  });
}

async function init(): Promise<void> {
  bridge = await waitForEvenAppBridge();
  console.warn('[Hermes] Bridge ready');

  await restoreState();
  await buildPage();
  registerEventHandler();
  connect();
}

init().catch((e) => {
  console.error('[Hermes] Init failed:', e);
});
