import { describe, it, expect } from 'vitest';
import { nextBackoffDelay, MAX_DELAY_MS } from '../src/lib/reconnect';

describe('nextBackoffDelay', () => {
  it('returns 1s on the first retry (attempts=0)', () => {
    expect(nextBackoffDelay(0)).toBe(1000);
  });

  it('doubles on each subsequent attempt', () => {
    expect(nextBackoffDelay(1)).toBe(2000);
    expect(nextBackoffDelay(2)).toBe(4000);
    expect(nextBackoffDelay(3)).toBe(8000);
    expect(nextBackoffDelay(4)).toBe(16000);
  });

  it('caps at MAX_DELAY_MS (30s)', () => {
    expect(nextBackoffDelay(5)).toBe(MAX_DELAY_MS);
    expect(nextBackoffDelay(10)).toBe(MAX_DELAY_MS);
    expect(nextBackoffDelay(100)).toBe(MAX_DELAY_MS);
  });

  it('crosses the cap exactly between attempts 4 and 5', () => {
    expect(nextBackoffDelay(4)).toBe(16000);
    expect(nextBackoffDelay(5)).toBe(MAX_DELAY_MS);
  });

  it('is monotonic non-decreasing', () => {
    let prev = 0;
    for (let i = 0; i < 20; i++) {
      const next = nextBackoffDelay(i);
      expect(next).toBeGreaterThanOrEqual(prev);
      prev = next;
    }
  });
});
