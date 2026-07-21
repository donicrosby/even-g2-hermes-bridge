// Thin wrapper re-exporting frame constructor names from the generated
// Protobuf stubs. Preserves the call-site names from the legacy `protocol.ts`
// so the rest of the glasses-app (main.ts) only needs an import path change:
//   from './protocol'  →  from './wire'
//
// Constructors return `Uint8Array` (serialized Frame) instead of JSON strings.
// Today this module is unused on the wire (the app still sends JSON); it will
// be wired in when the migration lands. Tests in `tests/wire.test.ts`
// exercise every constructor's round-trip so we can ship the wrapper ahead of
// the migration with confidence.

import type {
  Frame,
  HelloFrame,
  TextFrame,
  AudioStartFrame,
  AudioStopFrame,
  AudioDataFrame,
  SessionsListFrame,
  SessionsSwitchFrame,
  SessionsNewFrame,
  StopFrame,
  HelloOkFrame,
  AssistantDeltaFrame,
  AssistantFullFrame,
  ToolStartFrame,
  ToolEndFrame,
  TurnDoneFrame,
  SessionsFrame,
  SessionItem,
  ActiveFrame,
  HistoryFrame,
  HistoryItem,
  TranscriptFrame,
  ErrorFrame,
} from './proto_gen/hermes_bridge';

import * as FrameNS from './proto_gen/hermes_bridge';

// Re-export the generated types so callers can import them from `./wire`.
export type {
  Frame,
  HelloFrame,
  TextFrame,
  AudioStartFrame,
  AudioStopFrame,
  AudioDataFrame,
  SessionsListFrame,
  SessionsSwitchFrame,
  SessionsNewFrame,
  StopFrame,
  HelloOkFrame,
  AssistantDeltaFrame,
  AssistantFullFrame,
  ToolStartFrame,
  ToolEndFrame,
  TurnDoneFrame,
  SessionsFrame,
  SessionItem,
  ActiveFrame,
  HistoryFrame,
  HistoryItem,
  TranscriptFrame,
  ErrorFrame,
};

export class FrameParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'FrameParseError';
  }
}

// ts-proto generates `Frame` as both an interface AND a namespace containing
// `encode`/`decode`. The namespace lives on the same exported name; we use a
// qualified import to access the encode/decode functions type-safely.
const FrameCodec = FrameNS.Frame as unknown as {
  encode(message: Frame): { finish(): Uint8Array };
  decode(data: Uint8Array): Frame;
};

export function parseFrame(raw: Uint8Array): Frame {
  try {
    return FrameCodec.decode(raw);
  } catch (e) {
    throw new FrameParseError(e instanceof Error ? e.message : String(e));
  }
}

function encode(payload: Frame): Uint8Array {
  return FrameCodec.encode(payload).finish();
}

// ---- Inbound frame constructors (client → server) ----------------------

export function hello(token: string, device: string = 'g2'): Uint8Array {
  return encode({ hello: { token, device } });
}

export function text(content: string): Uint8Array {
  return encode({ text: { content } });
}

export function audioStart(): Uint8Array {
  return encode({ audioStart: {} });
}

export function audioStop(): Uint8Array {
  return encode({ audioStop: {} });
}

export function sessionsList(): Uint8Array {
  return encode({ sessionsList: {} });
}

export function sessionsSwitch(target: string): Uint8Array {
  return encode({ sessionsSwitch: { target } });
}

export function sessionsNew(): Uint8Array {
  return encode({ sessionsNew: {} });
}

export function stop(): Uint8Array {
  return encode({ stop: {} });
}

export function audioData(pcm: Uint8Array): Uint8Array {
  return encode({ audioData: { pcm } });
}

// ---- Outbound frame constructors (server → client) ----------------------

export function helloOk(opts: { active?: string; caps?: string[] } = {}): Uint8Array {
  const payload: HelloOkFrame = { caps: opts.caps ?? [] };
  if (opts.active !== undefined) payload.active = opts.active;
  return encode({ helloOk: payload });
}

export function assistantDelta(text: string): Uint8Array {
  return encode({ assistantDelta: { text } });
}

export function assistantFull(text: string): Uint8Array {
  return encode({ assistant: { text } });
}

export function toolStart(opts: { name: string; label?: string; emoji?: string }): Uint8Array {
  const payload: ToolStartFrame = { name: opts.name };
  if (opts.label !== undefined) payload.label = opts.label;
  if (opts.emoji !== undefined) payload.emoji = opts.emoji;
  return encode({ toolStart: payload });
}

export function toolEnd(opts: { name: string; ok?: boolean }): Uint8Array {
  return encode({ toolEnd: { name: opts.name, ok: opts.ok ?? true } });
}

export function turnDone(): Uint8Array {
  return encode({ turnDone: {} });
}

export function sessions(opts: { items: SessionItem[]; active?: string }): Uint8Array {
  const payload: SessionsFrame = { items: opts.items };
  if (opts.active !== undefined) payload.active = opts.active;
  return encode({ sessions: payload });
}

export function active(opts: { id: string; name?: string }): Uint8Array {
  const payload: ActiveFrame = { id: opts.id };
  if (opts.name !== undefined) payload.name = opts.name;
  return encode({ active: payload });
}

export function history(opts: { id: string; items: HistoryItem[]; ok?: boolean }): Uint8Array {
  return encode({
    history: { id: opts.id, items: opts.items, ok: opts.ok ?? true },
  });
}

export function transcript(text: string): Uint8Array {
  return encode({ transcript: { text } });
}

export function error(message: string): Uint8Array {
  return encode({ error: { msg: message } });
}
