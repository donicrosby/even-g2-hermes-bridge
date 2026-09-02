import { describe, it, expect } from 'vitest';
import { parseFrame, assistantDelta, assistantFull, turnDone } from '../src/wire';

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
    const d1 = parseFrame(assistantDelta('Hello '));
    const d2 = parseFrame(assistantDelta('world'));
    expect(d1.assistantDelta?.text).toBe('Hello ');
    expect(d2.assistantDelta?.text).toBe('world');
    // client-side append:
    let acc = '';
    for (const f of [d1, d2]) {
      if (f.assistantDelta?.text) acc += f.assistantDelta.text;
    }
    expect(acc).toBe('Hello world');
  });

  it('full frame replaces (resync)', () => {
    const delta = parseFrame(assistantDelta('old prefix'));
    const full = parseFrame(assistantFull('corrected full text'));
    let acc = '';
    if (delta.assistantDelta?.text) acc += delta.assistantDelta.text;
    if (full.assistant?.text) acc = full.assistant.text;
    expect(acc).toBe('corrected full text');
  });

  it('empty delta text is a no-op', () => {
    const f = parseFrame(assistantDelta(''));
    expect(f.assistantDelta?.text ?? '').toBe('');
  });

  it('turn.done separates turns (fresh accumulation)', () => {
    const d = parseFrame(assistantDelta('turn one text'));
    const t = parseFrame(turnDone());
    const d2 = parseFrame(assistantDelta('turn two'));
    expect(t.turnDone).toBeDefined();
    let acc = '';
    if (d.assistantDelta?.text) acc += d.assistantDelta.text;
    acc = ''; // handleTurnDone clears
    if (d2.assistantDelta?.text) acc += d2.assistantDelta.text;
    expect(acc).toBe('turn two');
  });
});
