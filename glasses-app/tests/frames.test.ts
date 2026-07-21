import { describe, it, expect } from 'vitest';
import { applyFrame, type Effect } from '../src/lib/frames';
import type { GlassesAppState } from '../src/lib/state';

const initialState: GlassesAppState = {
  accumulatedAssistantText: '',
  currentSessionId: '',
  currentSessionName: '',
  lastTranscript: '',
};

function effectKinds(effects: readonly Effect[]): string[] {
  return effects.map((e) => e.kind);
}

describe('applyFrame — hello.ok', () => {
  it('sets status to Connected', () => {
    const result = applyFrame(initialState, { t: 'hello.ok' });
    expect(effectKinds(result.effects)).toContain('setStatus');
    const setStatus = result.effects.find((e): e is Extract<Effect, { kind: 'setStatus' }> => e.kind === 'setStatus');
    expect(setStatus?.text).toBe('Connected');
  });

  it('updates currentSessionId when active is present', () => {
    const result = applyFrame(initialState, { t: 'hello.ok', active: 'sess-1' });
    expect(result.state.currentSessionId).toBe('sess-1');
    expect(effectKinds(result.effects)).toEqual(['setStatus', 'renderSession', 'scheduleSave']);
  });

  it('does not touch state when active is absent', () => {
    const result = applyFrame(initialState, { t: 'hello.ok' });
    expect(result.state).toEqual(initialState);
  });
});

describe('applyFrame — assistant.delta', () => {
  it('updates accumulatedAssistantText and renders', () => {
    const result = applyFrame(initialState, { t: 'assistant.delta', text: 'Hello' });
    expect(result.state.accumulatedAssistantText).toBe('Hello');
    expect(effectKinds(result.effects)).toEqual(['renderAssistant', 'maybeBringToFront']);
  });

  it('clears text when frame.text is empty', () => {
    const state = { ...initialState, accumulatedAssistantText: 'previous' };
    const result = applyFrame(state, { t: 'assistant.delta', text: '' });
    expect(result.state.accumulatedAssistantText).toBe('');
  });

  it('clears text when frame.text is missing', () => {
    const state = { ...initialState, accumulatedAssistantText: 'previous' };
    const result = applyFrame(state, { t: 'assistant.delta' });
    expect(result.state.accumulatedAssistantText).toBe('');
  });
});

describe('applyFrame — assistant (full)', () => {
  it('replaces accumulatedAssistantText and persists', () => {
    const result = applyFrame(
      { ...initialState, accumulatedAssistantText: 'partial' },
      { t: 'assistant', text: 'Full reply' },
    );
    expect(result.state.accumulatedAssistantText).toBe('Full reply');
    expect(effectKinds(result.effects)).toEqual(['renderAssistant', 'scheduleSave']);
  });
});

describe('applyFrame — tool.start', () => {
  it('uses label when present', () => {
    const result = applyFrame(initialState, {
      t: 'tool.start',
      name: 'web_search',
      label: 'Searching the web',
    });
    const setStatus = result.effects.find((e): e is Extract<Effect, { kind: 'setStatus' }> => e.kind === 'setStatus');
    expect(setStatus?.text).toBe('Searching the web');
  });

  it('falls back to name when label is absent', () => {
    const result = applyFrame(initialState, { t: 'tool.start', name: 'calc' });
    const setStatus = result.effects.find((e): e is Extract<Effect, { kind: 'setStatus' }> => e.kind === 'setStatus');
    expect(setStatus?.text).toBe('calc');
  });

  it('uses "Tool" when neither label nor name is present', () => {
    const result = applyFrame(initialState, { t: 'tool.start' });
    const setStatus = result.effects.find((e): e is Extract<Effect, { kind: 'setStatus' }> => e.kind === 'setStatus');
    expect(setStatus?.text).toBe('Tool');
  });
});

describe('applyFrame — tool.end', () => {
  it('clears the status line', () => {
    const result = applyFrame(initialState, { t: 'tool.end', name: 'web_search', ok: true });
    const setStatus = result.effects.find((e): e is Extract<Effect, { kind: 'setStatus' }> => e.kind === 'setStatus');
    expect(setStatus?.text).toBe('');
  });
});

describe('applyFrame — transcript', () => {
  it('updates lastTranscript and displays it', () => {
    const result = applyFrame(initialState, { t: 'transcript', text: 'hello world' });
    expect(result.state.lastTranscript).toBe('hello world');
    const setStatus = result.effects.find((e): e is Extract<Effect, { kind: 'setStatus' }> => e.kind === 'setStatus');
    expect(setStatus?.text).toBe('You said: hello world');
  });

  it('persists state after transcript', () => {
    const result = applyFrame(initialState, { t: 'transcript', text: 'x' });
    expect(effectKinds(result.effects)).toContain('scheduleSave');
  });
});

describe('applyFrame — turn.done', () => {
  it('clears accumulatedAssistantText', () => {
    const state = { ...initialState, accumulatedAssistantText: 'some reply' };
    const result = applyFrame(state, { t: 'turn.done' });
    expect(result.state.accumulatedAssistantText).toBe('');
  });

  it('schedules a save', () => {
    const result = applyFrame(initialState, { t: 'turn.done' });
    expect(effectKinds(result.effects)).toEqual(['scheduleSave']);
  });
});

describe('applyFrame — active', () => {
  it('updates session id and name', () => {
    const result = applyFrame(initialState, { t: 'active', id: 's1', name: 'First chat' });
    expect(result.state.currentSessionId).toBe('s1');
    expect(result.state.currentSessionName).toBe('First chat');
    expect(effectKinds(result.effects)).toEqual(['renderSession', 'scheduleSave']);
  });

  it('uses id as name when name is absent', () => {
    const result = applyFrame(initialState, { t: 'active', id: 's1' });
    expect(result.state.currentSessionName).toBe('s1');
  });
});

describe('applyFrame — error', () => {
  it('shows the error message', () => {
    const result = applyFrame(initialState, { t: 'error', msg: 'boom' });
    const setStatus = result.effects.find((e): e is Extract<Effect, { kind: 'setStatus' }> => e.kind === 'setStatus');
    expect(setStatus?.text).toBe('Error: boom');
  });

  it('shows "unknown" when msg is missing', () => {
    const result = applyFrame(initialState, { t: 'error' });
    const setStatus = result.effects.find((e): e is Extract<Effect, { kind: 'setStatus' }> => e.kind === 'setStatus');
    expect(setStatus?.text).toBe('Error: unknown');
  });
});

describe('applyFrame — unknown frame types', () => {
  it('returns state unchanged with no effects', () => {
    const result = applyFrame(initialState, { t: 'totally-bogus' });
    expect(result.state).toBe(initialState);
    expect(result.effects).toEqual([]);
  });

  it('handles empty object', () => {
    const result = applyFrame(initialState, {});
    expect(result.state).toBe(initialState);
    expect(result.effects).toEqual([]);
  });
});

describe('applyFrame — purity', () => {
  it('does not mutate the input state', () => {
    const state = { ...initialState };
    applyFrame(state, { t: 'assistant.delta', text: 'mutated?' });
    expect(state.accumulatedAssistantText).toBe('');
  });

  it('returns a new state object on each call', () => {
    const result = applyFrame(initialState, { t: 'assistant.delta', text: 'x' });
    expect(result.state).not.toBe(initialState);
  });
});
