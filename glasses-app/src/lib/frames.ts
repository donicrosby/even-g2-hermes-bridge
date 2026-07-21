/**
 * Pure frame reducer for inbound WS frames.
 *
 * main.ts keeps mutable state + side-effectful bridges (WS, SDK, rendering).
 * This module owns the *pure* reduction: given a frame and the current
 * state, return the new state plus a list of effects the caller should
 * perform (render, persist, activate). Keeping it pure makes the frame
 * dispatch testable without mocking the bridge or the DOM.
 */

import type {
  HelloOkFrame,
  AssistantDeltaFrame,
  AssistantFullFrame,
  ToolStartFrame,
  ToolEndFrame,
  TranscriptFrame,
  ActiveFrame,
  ErrorFrame,
} from '../protocol';
import type { GlassesAppState } from './state';

/** Side effects the reducer asks the caller to perform. */
export type Effect =
  | { readonly kind: 'renderAssistant' }
  | { readonly kind: 'setStatus'; readonly text: string }
  | { readonly kind: 'renderSession' }
  | { readonly kind: 'scheduleSave' }
  | { readonly kind: 'maybeBringToFront' };

/** Result of applying a frame: the new state and effects to execute. */
export type ApplyResult = {
  readonly state: GlassesAppState;
  readonly effects: readonly Effect[];
};

/** No-op result for unknown frames. */
function unchanged(state: GlassesAppState): ApplyResult {
  return { state, effects: [] };
}

/**
 * Apply an inbound frame to the HUD state.
 *
 * The frame is typed as `Record<string, unknown>` because it comes straight
 * from `JSON.parse`. The reducer narrows by the `t` discriminator and casts
 * to the protocol.ts interfaces. Unknown `t` values return the state
 * unchanged with no effects.
 */
export function applyFrame(
  state: GlassesAppState,
  frame: Record<string, unknown>,
): ApplyResult {
  const t = frame.t as string;
  switch (t) {
    case 'hello.ok':
      return applyHelloOk(state, frame as unknown as HelloOkFrame);
    case 'assistant.delta':
      return applyAssistantDelta(state, frame as unknown as AssistantDeltaFrame);
    case 'assistant':
      return applyAssistantFull(state, frame as unknown as AssistantFullFrame);
    case 'tool.start':
      return applyToolStart(state, frame as unknown as ToolStartFrame);
    case 'tool.end':
      return applyToolEnd(state, frame as unknown as ToolEndFrame);
    case 'transcript':
      return applyTranscript(state, frame as unknown as TranscriptFrame);
    case 'turn.done':
      return applyTurnDone(state);
    case 'active':
      return applyActive(state, frame as unknown as ActiveFrame);
    case 'error':
      return applyError(state, frame as unknown as ErrorFrame);
    default:
      return unchanged(state);
  }
}

function applyHelloOk(state: GlassesAppState, frame: HelloOkFrame): ApplyResult {
  if (!frame.active) {
    return { state, effects: [{ kind: 'setStatus', text: 'Connected' }] };
  }
  return {
    state: { ...state, currentSessionId: frame.active },
    effects: [
      { kind: 'setStatus', text: 'Connected' },
      { kind: 'renderSession' },
      { kind: 'scheduleSave' },
    ],
  };
}

function applyAssistantDelta(state: GlassesAppState, frame: AssistantDeltaFrame): ApplyResult {
  return {
    state: { ...state, accumulatedAssistantText: frame.text || '' },
    effects: [
      { kind: 'renderAssistant' },
      { kind: 'maybeBringToFront' },
    ],
  };
}

function applyAssistantFull(state: GlassesAppState, frame: AssistantFullFrame): ApplyResult {
  return {
    state: { ...state, accumulatedAssistantText: frame.text || '' },
    effects: [
      { kind: 'renderAssistant' },
      { kind: 'scheduleSave' },
    ],
  };
}

function applyToolStart(state: GlassesAppState, frame: ToolStartFrame): ApplyResult {
  const label = ('label' in frame && frame.label) || frame.name || 'Tool';
  return { state, effects: [{ kind: 'setStatus', text: label }] };
}

function applyToolEnd(state: GlassesAppState, _frame: ToolEndFrame): ApplyResult {
  return { state, effects: [{ kind: 'setStatus', text: '' }] };
}

function applyTranscript(state: GlassesAppState, frame: TranscriptFrame): ApplyResult {
  const text = frame.text || '';
  return {
    state: { ...state, lastTranscript: text },
    effects: [
      { kind: 'setStatus', text: `You said: ${text}` },
      { kind: 'scheduleSave' },
    ],
  };
}

function applyTurnDone(state: GlassesAppState): ApplyResult {
  return {
    state: { ...state, accumulatedAssistantText: '' },
    effects: [{ kind: 'scheduleSave' }],
  };
}

function applyActive(state: GlassesAppState, frame: ActiveFrame): ApplyResult {
  const id = frame.id;
  const name = ('name' in frame && frame.name) || id;
  return {
    state: { ...state, currentSessionId: id, currentSessionName: name },
    effects: [
      { kind: 'renderSession' },
      { kind: 'scheduleSave' },
    ],
  };
}

function applyError(state: GlassesAppState, frame: ErrorFrame): ApplyResult {
  return { state, effects: [{ kind: 'setStatus', text: `Error: ${frame.msg || 'unknown'}` }] };
}
