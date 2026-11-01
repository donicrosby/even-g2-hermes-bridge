import { describe, it, expect } from 'vitest';
import {
  parseOutbound,
  type AssistantDeltaFrame,
  type AssistantFullFrame,
  type TurnDoneFrame,
} from '../src/protocol';

/** Round-trip a JSON frame through parseOutbound (the inbound path). */
const parse = (raw: unknown): ReturnType<typeof parseOutbound> =>
  parseOutbound(JSON.stringify(raw));

const delta = (text: string): AssistantDeltaFrame =>
  parse({ t: 'assistant.delta', text }) as AssistantDeltaFrame;

const full = (text: string): AssistantFullFrame =>
  parse({ t: 'assistant', text }) as AssistantFullFrame;

const turnDone = (): TurnDoneFrame =>
  parse({ t: 'turn.done' }) as TurnDoneFrame;

/**
 * Delta accumulation semantics (openspec strip-markdown-plain-text-output):
 *
 * assistant.delta carries a SUFFIX — the client appends it to the
 * accumulated text. assistant.full carries the complete text — the client
 * replaces wholesale (resync after prefix divergence, e.g. markdown
 * stripping rewriting an earlier span). turn.done clears accumulation for
 * the next turn.
 *
 * These tests pin the frame-level contract; main.ts handleAssistantDelta
 * implements the append side (verified in page-lifecycle tests via
 * handleFrame dispatch).
 */
describe('assistant text accumulation contract', () => {
  it('sequential deltas append', () => {
    const d1 = delta('Hello ');
    const d2 = delta('world');
    expect(d1.text).toBe('Hello ');
    expect(d2.text).toBe('world');
    // client-side append:
    let acc = '';
    for (const f of [d1, d2]) {
      if (f.text) acc += f.text;
    }
    expect(acc).toBe('Hello world');
  });

  it('full frame replaces (resync)', () => {
    const d = delta('old prefix');
    const f = full('corrected full text');
    let acc = '';
    if (d.text) acc += d.text;
    acc = f.text;
    expect(acc).toBe('corrected full text');
  });

  it('empty delta text is a no-op', () => {
    const f = delta('');
    expect(f.text ?? '').toBe('');
  });

  it('turn.done separates turns (fresh accumulation)', () => {
    const d = delta('turn one text');
    const t = turnDone();
    const d2 = delta('turn two');
    expect(t.t).toBe('turn.done');
    let acc = '';
    if (d.text) acc += d.text;
    acc = ''; // handleTurnDone clears
    if (d2.text) acc += d2.text;
    expect(acc).toBe('turn two');
  });
});
