import { describe, it, expect } from 'vitest';
import {
  serializeState,
  parseState,
  mergeState,
  STATE_KEY,
  type GlassesAppState,
} from '../src/lib/state';

const empty: GlassesAppState = {
  bridgeUrl: '',
  bridgeToken: '',
  voiceConfirmMode: 'careful',
  voiceAutoSendSec: 15,
  accumulatedAssistantText: '',
  currentSessionId: '',
  currentSessionName: '',
  lastTranscript: '',
  knownSessions: [],
};

const populated: GlassesAppState = {
  bridgeUrl: 'wss://hermes.example.com',
  bridgeToken: 'secret-token',
  voiceConfirmMode: 'fast',
  voiceAutoSendSec: 0,
  accumulatedAssistantText: 'Hello world',
  currentSessionId: 'sess-1',
  currentSessionName: 'First chat',
  lastTranscript: 'hi there',
  knownSessions: [{ id: 'sess-1', name: 'First chat' }],
};

describe('serializeState', () => {
  it('produces valid JSON with all fields', () => {
    const raw = serializeState(populated);
    const parsed = JSON.parse(raw);
    expect(parsed).toEqual(populated);
  });

  it('returns a snapshot, not a live reference', () => {
    const state = { ...populated };
    const raw = serializeState(state);
    state.accumulatedAssistantText = 'mutated';
    const reparsed = JSON.parse(raw);
    expect(reparsed.accumulatedAssistantText).toBe('Hello world');
  });

  it('handles empty state', () => {
    const raw = serializeState(empty);
    expect(JSON.parse(raw)).toEqual(empty);
  });
});

describe('parseState', () => {
  it('round-trips serializeState output', () => {
    const raw = serializeState(populated);
    const parsed = parseState(raw);
    expect(parsed).toEqual(populated);
  });

  it('returns null for malformed JSON', () => {
    expect(parseState('{not json')).toBeNull();
  });

  it('returns null for empty string', () => {
    expect(parseState('')).toBeNull();
  });

  it('returns a partial when fields are missing', () => {
    const parsed = parseState(JSON.stringify({ currentSessionId: 'x' }));
    expect(parsed?.currentSessionId).toBe('x');
    expect(parsed?.accumulatedAssistantText).toBeUndefined();
  });
});

describe('mergeState', () => {
  it('overrides fields present in the snapshot', () => {
    const merged = mergeState(populated, {
      accumulatedAssistantText: 'new text',
    });
    expect(merged.accumulatedAssistantText).toBe('new text');
    expect(merged.currentSessionId).toBe('sess-1');
  });

  it('preserves target fields when snapshot field is missing', () => {
    const merged = mergeState(populated, { currentSessionId: 'sess-2' });
    expect(merged.currentSessionId).toBe('sess-2');
    expect(merged.currentSessionName).toBe('First chat');
    expect(merged.lastTranscript).toBe('hi there');
  });

  it('returns the target unchanged when snapshot is null', () => {
    const merged = mergeState(populated, null);
    expect(merged).toEqual(populated);
  });

  it('returns a new object, not a mutation of the target', () => {
    const target = { ...populated };
    const merged = mergeState(target, { lastTranscript: 'changed' });
    expect(target.lastTranscript).toBe('hi there');
    expect(merged.lastTranscript).toBe('changed');
  });

  it('treats undefined snapshot fields as missing (?? semantics)', () => {
    const merged = mergeState(populated, {
      currentSessionName: undefined,
    });
    expect(merged.currentSessionName).toBe('First chat');
  });
});

describe('STATE_KEY', () => {
  it('is the documented key string', () => {
    expect(STATE_KEY).toBe('glassesAppState');
  });
});
