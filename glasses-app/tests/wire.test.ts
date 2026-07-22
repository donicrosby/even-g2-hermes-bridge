import { describe, it, expect } from 'vitest';
import {
  parseFrame,
  FrameParseError,
  hello,
  text,
  audioStart,
  audioStop,
  audioData,
  sessionsList,
  sessionsSwitch,
  sessionsNew,
  stop,
  helloOk,
  assistantDelta,
  assistantFull,
  toolStart,
  toolEnd,
  turnDone,
  sessions,
  active,
  history,
  transcript,
  error,
} from '../src/wire';

describe('inbound constructors (client → server)', () => {
  it('hello includes token and device', () => {
    const frame = parseFrame(hello('tok123', 'g2-serial'));
    expect(frame.hello?.token).toBe('tok123');
    expect(frame.hello?.device).toBe('g2-serial');
  });

  it('hello defaults device to g2', () => {
    const frame = parseFrame(hello('tok123'));
    expect(frame.hello?.device).toBe('g2');
  });

  it('text carries content', () => {
    const frame = parseFrame(text('hello world'));
    expect(frame.text?.content).toBe('hello world');
  });

  it('text preserves utf8', () => {
    const frame = parseFrame(text('héllo wörld 🎉'));
    expect(frame.text?.content).toBe('héllo wörld 🎉');
  });

  it('audioStart produces an audioStart variant', () => {
    const frame = parseFrame(audioStart());
    expect(frame.audioStart).toBeDefined();
  });

  it('audioStop produces an audioStop variant', () => {
    const frame = parseFrame(audioStop());
    expect(frame.audioStop).toBeDefined();
  });

  it('audioData carries pcm bytes', () => {
    const pcm = new Uint8Array([0, 128, 255, 1, 2, 3]);
    const frame = parseFrame(audioData(pcm));
    expect(Array.from(frame.audioData?.pcm ?? [])).toEqual(Array.from(pcm));
  });

  it('sessionsList produces a sessionsList variant', () => {
    const frame = parseFrame(sessionsList());
    expect(frame.sessionsList).toBeDefined();
  });

  it('sessionsSwitch carries target', () => {
    const frame = parseFrame(sessionsSwitch('+1'));
    expect(frame.sessionsSwitch?.target).toBe('+1');
  });

  it('sessionsNew produces a sessionsNew variant', () => {
    const frame = parseFrame(sessionsNew());
    expect(frame.sessionsNew).toBeDefined();
  });

  it('stop produces a stop variant', () => {
    const frame = parseFrame(stop());
    expect(frame.stop).toBeDefined();
  });
});

describe('outbound constructors (server → client)', () => {
  it('helloOk minimal', () => {
    const frame = parseFrame(helloOk());
    expect(frame.helloOk).toBeDefined();
    expect(frame.helloOk?.active).toBeUndefined();
    expect(frame.helloOk?.caps).toEqual([]);
  });

  it('helloOk with active', () => {
    const frame = parseFrame(helloOk({ active: 'sess-1' }));
    expect(frame.helloOk?.active).toBe('sess-1');
  });

  it('helloOk with caps', () => {
    const frame = parseFrame(helloOk({ caps: ['streaming', 'sessions'] }));
    expect(frame.helloOk?.caps).toEqual(['streaming', 'sessions']);
  });

  it('assistantDelta carries text', () => {
    const frame = parseFrame(assistantDelta('Hello '));
    expect(frame.assistantDelta?.text).toBe('Hello ');
  });

  it('assistantFull carries text', () => {
    const frame = parseFrame(assistantFull('Hello world'));
    expect(frame.assistant?.text).toBe('Hello world');
  });

  it('toolStart minimal', () => {
    const frame = parseFrame(toolStart({ name: 'web_search' }));
    expect(frame.toolStart?.name).toBe('web_search');
    expect(frame.toolStart?.label).toBeUndefined();
    expect(frame.toolStart?.emoji).toBeUndefined();
  });

  it('toolStart with label and emoji', () => {
    const frame = parseFrame(
      toolStart({ name: 'web_search', label: 'Searching the web', emoji: '🔍' }),
    );
    expect(frame.toolStart?.label).toBe('Searching the web');
    expect(frame.toolStart?.emoji).toBe('🔍');
  });

  it('toolEnd defaults ok to true', () => {
    const frame = parseFrame(toolEnd({ name: 'web_search' }));
    expect(frame.toolEnd?.name).toBe('web_search');
    expect(frame.toolEnd?.ok).toBe(true);
  });

  it('toolEnd with ok false', () => {
    const frame = parseFrame(toolEnd({ name: 'web_search', ok: false }));
    expect(frame.toolEnd?.ok).toBe(false);
  });

  it('turnDone produces a turnDone variant', () => {
    const frame = parseFrame(turnDone());
    expect(frame.turnDone).toBeDefined();
  });

  it('sessions minimal', () => {
    const frame = parseFrame(sessions({ items: [] }));
    expect(frame.sessions?.items).toEqual([]);
    expect(frame.sessions?.active).toBeUndefined();
  });

  it('sessions with items and active', () => {
    const items = [
      { id: 's1', name: 'First' },
      { id: 's2', name: 'Second' },
    ];
    const frame = parseFrame(sessions({ items, active: 's2' }));
    expect(frame.sessions?.items.length).toBe(2);
    expect(frame.sessions?.items[0]?.id).toBe('s1');
    expect(frame.sessions?.items[0]?.name).toBe('First');
    expect(frame.sessions?.items[1]?.id).toBe('s2');
    expect(frame.sessions?.active).toBe('s2');
  });

  it('active minimal', () => {
    const frame = parseFrame(active({ id: 's1' }));
    expect(frame.active?.id).toBe('s1');
    expect(frame.active?.name).toBeUndefined();
  });

  it('active with name', () => {
    const frame = parseFrame(active({ id: 's1', name: 'First chat' }));
    expect(frame.active?.name).toBe('First chat');
  });

  it('history minimal', () => {
    const frame = parseFrame(history({ id: 's1', items: [] }));
    expect(frame.history?.id).toBe('s1');
    expect(frame.history?.ok).toBe(true);
  });

  it('history with items and ok false', () => {
    const items = [
      { role: 'user', content: 'hi' },
      { role: 'assistant', content: 'hello' },
    ];
    const frame = parseFrame(history({ id: 's1', items, ok: false }));
    expect(frame.history?.items.length).toBe(2);
    expect(frame.history?.items[0]?.role).toBe('user');
    expect(frame.history?.items[0]?.content).toBe('hi');
    expect(frame.history?.ok).toBe(false);
  });

  it('transcript carries text', () => {
    const frame = parseFrame(transcript('the user said this'));
    expect(frame.transcript?.text).toBe('the user said this');
  });

  it('error carries message', () => {
    const frame = parseFrame(error('something broke'));
    expect(frame.error?.msg).toBe('something broke');
  });
});

describe('parseFrame error paths', () => {
  it('raises FrameParseError on bytes with invalid length prefix', () => {
    const bad = new Uint8Array([0x0a, 0xff, 0xff, 0xff, 0xff, 0x7f]);
    expect(() => parseFrame(bad)).toThrow(FrameParseError);
  });

  it('raises FrameParseError on truncated frame', () => {
    const valid = text('hello');
    const truncated = valid.subarray(0, Math.floor(valid.length / 2));
    expect(() => parseFrame(truncated)).toThrow(FrameParseError);
  });

  it('FrameParseError is an Error subclass', () => {
    expect(new FrameParseError('msg')).toBeInstanceOf(Error);
  });
});

describe('wire invariants', () => {
  it('every constructor returns Uint8Array', () => {
    const outputs: Uint8Array[] = [
      hello('t'),
      text('t'),
      audioStart(),
      audioStop(),
      audioData(new Uint8Array([0, 1])),
      sessionsList(),
      sessionsSwitch('+1'),
      sessionsNew(),
      stop(),
      helloOk(),
      assistantDelta('t'),
      assistantFull('t'),
      toolStart({ name: 'n' }),
      toolEnd({ name: 'n' }),
      turnDone(),
      sessions({ items: [] }),
      active({ id: 'id' }),
      history({ id: 'id', items: [] }),
      transcript('t'),
      error('t'),
    ];
    for (const out of outputs) {
      expect(out).toBeInstanceOf(Uint8Array);
    }
  });

  it('round-trip preserves the oneof discriminator for every variant', () => {
    type Sample = ReadonlyArray<
      readonly [Uint8Array, (f: ReturnType<typeof parseFrame>) => boolean]
    >;
    const samples: Sample = [
      [hello('t'), (f) => !!f.hello],
      [text('t'), (f) => !!f.text],
      [audioStart(), (f) => !!f.audioStart],
      [audioStop(), (f) => !!f.audioStop],
      [audioData(new Uint8Array([0])), (f) => !!f.audioData],
      [sessionsList(), (f) => !!f.sessionsList],
      [sessionsSwitch('+1'), (f) => !!f.sessionsSwitch],
      [sessionsNew(), (f) => !!f.sessionsNew],
      [stop(), (f) => !!f.stop],
      [helloOk(), (f) => !!f.helloOk],
      [assistantDelta('t'), (f) => !!f.assistantDelta],
      [assistantFull('t'), (f) => !!f.assistant],
      [toolStart({ name: 'n' }), (f) => !!f.toolStart],
      [toolEnd({ name: 'n' }), (f) => !!f.toolEnd],
      [turnDone(), (f) => !!f.turnDone],
      [sessions({ items: [] }), (f) => !!f.sessions],
      [active({ id: 'id' }), (f) => !!f.active],
      [history({ id: 'id', items: [] }), (f) => !!f.history],
      [transcript('t'), (f) => !!f.transcript],
      [error('t'), (f) => !!f.error],
    ];
    for (const [serialized, variantIsSet] of samples) {
      const frame = parseFrame(serialized);
      expect(variantIsSet(frame)).toBe(true);
    }
  });
});
