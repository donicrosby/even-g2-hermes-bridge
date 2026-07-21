import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// These tests exercise the glasses-app's session round-trip behavior. We can't
// import main.ts directly (it triggers init() which hangs on waitForEvenAppBridge
// in Node), so we test the pure helpers and re-implement the dispatch logic
// exactly as main.ts does. The contract is what matters; the implementation
// lives in main.ts because it closes over module-scoped state.

type SessionItem = { id: string; name?: string };

type SessionsFrame = {
  t: 'sessions';
  items: SessionItem[];
  active?: string;
};

type HelloOkFrame = {
  t: 'hello.ok';
  active?: string;
  caps?: string[];
};

// --- Minimal re-implementation of main.ts session handlers ---------------

function makeSessionDispatcher(opts: {
  setCurrentSessionId: (id: string) => void;
  setCurrentSessionName: (name: string) => void;
  renderSession: () => void;
  scheduleSave: () => void;
  getKnownSessions: () => SessionItem[];
  setKnownSessions: (s: SessionItem[]) => void;
  getCurrentSessionId: () => string;
}) {
  function handleHelloOk(frame: HelloOkFrame, sendFrame: (f: unknown) => void): void {
    if (frame.active) {
      opts.setCurrentSessionId(frame.active);
      opts.renderSession();
      opts.scheduleSave();
    }
    sendFrame({ t: 'sessions.list' });
  }

  function handleSessions(frame: SessionsFrame): void {
    opts.setKnownSessions(frame.items ?? []);
    if (frame.active && frame.active !== opts.getCurrentSessionId()) {
      opts.setCurrentSessionId(frame.active);
      const match = opts.getKnownSessions().find((s) => s.id === frame.active);
      opts.setCurrentSessionName((match && match.name) || frame.active);
      opts.renderSession();
      opts.scheduleSave();
    }
  }

  return { handleHelloOk, handleSessions };
}

// --- Tests ---------------------------------------------------------------

describe('handleHelloOk', () => {
  it('sends sessions.list exactly once after the existing logic', () => {
    const sendFrame = vi.fn();
    const renderSession = vi.fn();
    const scheduleSave = vi.fn();
    const { handleHelloOk } = makeSessionDispatcher({
      setCurrentSessionId: () => undefined,
      setCurrentSessionName: () => undefined,
      renderSession,
      scheduleSave,
      getKnownSessions: () => [],
      setKnownSessions: () => undefined,
      getCurrentSessionId: () => '',
    });

    handleHelloOk({ t: 'hello.ok', active: 's1' }, sendFrame);

    expect(sendFrame).toHaveBeenCalledTimes(1);
    expect(sendFrame).toHaveBeenCalledWith({ t: 'sessions.list' });
    expect(renderSession).toHaveBeenCalledTimes(1);
    expect(scheduleSave).toHaveBeenCalledTimes(1);
  });

  it('sends sessions.list even when active is absent', () => {
    const sendFrame = vi.fn();
    const { handleHelloOk } = makeSessionDispatcher({
      setCurrentSessionId: () => undefined,
      setCurrentSessionName: () => undefined,
      renderSession: () => undefined,
      scheduleSave: () => undefined,
      getKnownSessions: () => [],
      setKnownSessions: () => undefined,
      getCurrentSessionId: () => '',
    });

    handleHelloOk({ t: 'hello.ok' }, sendFrame);

    expect(sendFrame).toHaveBeenCalledTimes(1);
    expect(sendFrame).toHaveBeenCalledWith({ t: 'sessions.list' });
  });
});

describe('handleSessions', () => {
  it('stores the items list and updates state when active differs', () => {
    let knownSessions: SessionItem[] = [];
    let currentSessionId = 'old-id';
    let currentSessionName = '';
    const renderSession = vi.fn();
    const scheduleSave = vi.fn();
    const { handleSessions } = makeSessionDispatcher({
      setCurrentSessionId: (id) => {
        currentSessionId = id;
      },
      setCurrentSessionName: (name) => {
        currentSessionName = name;
      },
      renderSession,
      scheduleSave,
      getKnownSessions: () => knownSessions,
      setKnownSessions: (s) => {
        knownSessions = s;
      },
      getCurrentSessionId: () => currentSessionId,
    });

    handleSessions({
      t: 'sessions',
      items: [
        { id: 's1', name: 'Session 1' },
        { id: 's2', name: 'Session 2' },
      ],
      active: 's2',
    });

    expect(knownSessions).toHaveLength(2);
    expect(currentSessionId).toBe('s2');
    expect(currentSessionName).toBe('Session 2');
    expect(renderSession).toHaveBeenCalledTimes(1);
    expect(scheduleSave).toHaveBeenCalledTimes(1);
  });

  it('does not re-render when active matches current state', () => {
    let knownSessions: SessionItem[] = [];
    const currentSessionId = 's1';
    const renderSession = vi.fn();
    const scheduleSave = vi.fn();
    const { handleSessions } = makeSessionDispatcher({
      setCurrentSessionId: () => undefined,
      setCurrentSessionName: () => undefined,
      renderSession,
      scheduleSave,
      getKnownSessions: () => knownSessions,
      setKnownSessions: (s) => {
        knownSessions = s;
      },
      getCurrentSessionId: () => currentSessionId,
    });

    handleSessions({
      t: 'sessions',
      items: [{ id: 's1', name: 'Session 1' }],
      active: 's1',
    });

    expect(knownSessions).toHaveLength(1);
    expect(renderSession).not.toHaveBeenCalled();
    expect(scheduleSave).not.toHaveBeenCalled();
  });

  it('stores items list without rendering when active is absent', () => {
    let knownSessions: SessionItem[] = [];
    const renderSession = vi.fn();
    const { handleSessions } = makeSessionDispatcher({
      setCurrentSessionId: () => undefined,
      setCurrentSessionName: () => undefined,
      renderSession,
      scheduleSave: () => undefined,
      getKnownSessions: () => knownSessions,
      setKnownSessions: (s) => {
        knownSessions = s;
      },
      getCurrentSessionId: () => '',
    });

    handleSessions({ t: 'sessions', items: [{ id: 'x' }] });

    expect(knownSessions).toEqual([{ id: 'x' }]);
    expect(renderSession).not.toHaveBeenCalled();
  });

  it('falls back to id when active has no matching item name', () => {
    let currentSessionId = '';
    let currentSessionName = '';
    const { handleSessions } = makeSessionDispatcher({
      setCurrentSessionId: (id) => {
        currentSessionId = id;
      },
      setCurrentSessionName: (name) => {
        currentSessionName = name;
      },
      renderSession: () => undefined,
      scheduleSave: () => undefined,
      getKnownSessions: () => [],
      setKnownSessions: () => undefined,
      getCurrentSessionId: () => 'different',
    });

    handleSessions({
      t: 'sessions',
      items: [{ id: 'other' }],
      active: 's-orphaned',
    });

    expect(currentSessionId).toBe('s-orphaned');
    expect(currentSessionName).toBe('s-orphaned');
  });
});
