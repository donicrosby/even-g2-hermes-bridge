import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  BridgeTimeoutError,
  withBridgeTimeout,
  createBridgeQueue,
} from '../src/lib/bridge';

describe('withBridgeTimeout', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('resolves with the underlying value when the promise settles before ms', async () => {
    const p = Promise.resolve('ok');
    await expect(withBridgeTimeout(p, 1000)).resolves.toBe('ok');
  });

  it('rejects with BridgeTimeoutError when the promise settles after ms', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const p = new Promise<string>((resolve) => {
      setTimeout(() => resolve('late'), 10_000);
    });
    const pending = withBridgeTimeout(p, 1000);
    vi.advanceTimersByTime(1000);
    await expect(pending).rejects.toBeInstanceOf(BridgeTimeoutError);
    expect(warnSpy).toHaveBeenCalledWith('[Hermes] bridge call timed out after 1000ms');
    warnSpy.mockRestore();
  });

  it('clears the timeout timer when the underlying promise settles first', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const p = Promise.resolve('fast');
    await withBridgeTimeout(p, 5000);
    vi.advanceTimersByTime(10_000);
    expect(warnSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
  });

  it('propagates the underlying rejection', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const p = Promise.reject(new Error('boom'));
    const pending = withBridgeTimeout(p, 5000);
    vi.advanceTimersByTime(0);
    await expect(pending).rejects.toThrow('boom');
    expect(warnSpy).not.toHaveBeenCalled();
    warnSpy.mockRestore();
  });
});

describe('createBridgeQueue().serializedBridge', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('runs two calls in declaration order', async () => {
    const queue = createBridgeQueue();
    const order: string[] = [];
    const slow = new Promise<string>((resolve) => {
      setTimeout(() => {
        order.push('slow-done');
        resolve('slow');
      }, 1000);
    });
    const slowPromise = queue.serializedBridge(() => slow).then((v) => {
      order.push(`slow-resolved:${v}`);
    });
    const fastPromise = queue
      .serializedBridge(() => Promise.resolve('fast'))
      .then((v) => {
        order.push(`fast-resolved:${v}`);
      });

    vi.advanceTimersByTime(1000);
    await Promise.all([slowPromise, fastPromise]);

    expect(order).toEqual([
      'slow-done',
      'slow-resolved:slow',
      'fast-resolved:fast',
    ]);
  });

  it('does not let a rejected first call block the second call', async () => {
    const queue = createBridgeQueue();
    const order: string[] = [];
    const firstPromise = queue
      .serializedBridge(() => Promise.reject(new Error('first-fail')))
      .then(
        () => order.push('first-ok'),
        () => order.push('first-err'),
      );
    const secondPromise = queue
      .serializedBridge(() => Promise.resolve('second-ok'))
      .then((v) => order.push(`second-resolved:${v}`));

    await vi.runAllTimersAsync();
    await Promise.all([firstPromise, secondPromise]);

    expect(order).toContain('first-err');
    expect(order).toContain('second-resolved:second-ok');
    expect(order.indexOf('first-err')).toBeLessThan(order.indexOf('second-resolved:second-ok'));
  });

  it('remains usable after multiple failures', async () => {
    const queue = createBridgeQueue();
    const p1 = queue.serializedBridge(() => Promise.reject(new Error('e1'))).catch(() => undefined);
    const p2 = queue.serializedBridge(() => Promise.reject(new Error('e2'))).catch(() => undefined);
    const p3 = queue.serializedBridge(() => Promise.reject(new Error('e3'))).catch(() => undefined);
    await vi.runAllTimersAsync();
    await Promise.all([p1, p2, p3]);

    const finalVal = await queue.serializedBridge(() => Promise.resolve('still-works'));
    expect(finalVal).toBe('still-works');
  });
});

describe('createBridgeQueue().runBridge', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns the underlying value on success', async () => {
    const queue = createBridgeQueue();
    const result = await queue.runBridge('test', () => Promise.resolve(42));
    expect(result).toBe(42);
  });

  it('returns undefined and logs a warning on timeout', async () => {
    const queue = createBridgeQueue();
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const slow = new Promise<string>(() => undefined);
    const pending = queue.runBridge('test', () => slow);
    await vi.advanceTimersByTimeAsync(5000);
    expect(await pending).toBeUndefined();
    expect(warnSpy).toHaveBeenCalledWith(
      '[Hermes] test failed:',
      expect.any(BridgeTimeoutError),
    );
    warnSpy.mockRestore();
  });

  it('returns undefined and logs a warning when the thunk rejects', async () => {
    const queue = createBridgeQueue();
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const result = await queue.runBridge('test', () =>
      Promise.reject(new Error('underlying boom')),
    );
    expect(result).toBeUndefined();
    expect(warnSpy).toHaveBeenCalledWith('[Hermes] test failed:', expect.any(Error));
    warnSpy.mockRestore();
  });

  it('returns undefined and logs a warning when the thunk throws synchronously', async () => {
    const queue = createBridgeQueue();
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const result = await queue.runBridge('test', () => {
      throw new Error('sync boom');
    });
    expect(result).toBeUndefined();
    expect(warnSpy).toHaveBeenCalledWith('[Hermes] test failed:', expect.any(Error));
    warnSpy.mockRestore();
  });

  it('serializes calls so two thunks never run concurrently', async () => {
    const queue = createBridgeQueue();
    let inFlight = 0;
    let maxInFlight = 0;

    const makeThunk = (label: string, delay: number) => (): Promise<string> => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      return new Promise<string>((resolve) => {
        setTimeout(() => {
          inFlight -= 1;
          resolve(label);
        }, delay);
      });
    };

    const p1 = queue.runBridge('a', makeThunk('a', 1000));
    const p2 = queue.runBridge('b', makeThunk('b', 500));
    const p3 = queue.runBridge('c', makeThunk('c', 200));

    await vi.advanceTimersByTimeAsync(2000);
    const [a, b, c] = await Promise.all([p1, p2, p3]);

    expect(maxInFlight).toBe(1);
    expect([a, b, c]).toEqual(['a', 'b', 'c']);
  });
});

describe('createBridgeQueue().reset', () => {
  it('re-initializes the chain so subsequent calls work after a reset', async () => {
    const queue = createBridgeQueue();
    const first = queue.runBridge('a', () => Promise.resolve('a'));
    expect(await first).toBe('a');
    queue.reset();
    const second = queue.runBridge('b', () => Promise.resolve('b'));
    expect(await second).toBe('b');
  });
});
