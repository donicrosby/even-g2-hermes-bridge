/**
 * Snapshot serialize/parse for the glasses-app HUD state.
 *
 * The state is persisted via `bridge.setLocalStorage` so it survives
 * background/foreground transitions (SDK 0.0.12 lacks the
 * setBackgroundState/onBackgroundRestore APIs). This module owns the
 * JSON shape so encode/decode can be tested independently of the bridge.
 */

/** Item shape mirroring SessionsFrame.items from protocol.ts. */
export type SessionItem = {
  id: string;
  name?: string;
};

/** Mutable state tracked across background transitions. */
export type GlassesAppState = {
  accumulatedAssistantText: string;
  currentSessionId: string;
  currentSessionName: string;
  lastTranscript: string;
  knownSessions: SessionItem[];
};

export const STATE_KEY = 'glassesAppState';

/** State with all fields optional — what we get back from JSON.parse. */
type PartialState = {
  [K in keyof GlassesAppState]?: GlassesAppState[K];
};

/**
 * Serialize state to a JSON string for setLocalStorage.
 *
 * Returns a fresh snapshot — safe to call on a live state object.
 */
export function serializeState(state: GlassesAppState): string {
  return JSON.stringify({
    accumulatedAssistantText: state.accumulatedAssistantText,
    currentSessionId: state.currentSessionId,
    currentSessionName: state.currentSessionName,
    lastTranscript: state.lastTranscript,
    knownSessions: state.knownSessions,
  });
}

/**
 * Parse a stored JSON snapshot, applying `??` fallbacks for missing fields.
 *
 * Malformed JSON returns null — the caller should treat that as "no state"
 * rather than crashing the init flow.
 */
export function parseState(raw: string): PartialState | null {
  try {
    const parsed = JSON.parse(raw) as PartialState;
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Merge a parsed snapshot into a target state, using each field's existing
 * value as the fallback when the snapshot field is missing.
 *
 * Mirrors the `??` pattern the spec calls for in onBackgroundRestore.
 */
export function mergeState(
  target: GlassesAppState,
  snapshot: PartialState | null,
): GlassesAppState {
  if (!snapshot) return { ...target };
  return {
    accumulatedAssistantText: snapshot.accumulatedAssistantText ?? target.accumulatedAssistantText,
    currentSessionId: snapshot.currentSessionId ?? target.currentSessionId,
    currentSessionName: snapshot.currentSessionName ?? target.currentSessionName,
    lastTranscript: snapshot.lastTranscript ?? target.lastTranscript,
    knownSessions: snapshot.knownSessions ?? target.knownSessions,
  };
}
