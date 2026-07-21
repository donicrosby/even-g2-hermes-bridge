/**
 * Reconnect backoff calculation for the WS connection.
 *
 * Exponential backoff with a cap: each attempt doubles the delay (1s, 2s,
 * 4s, ...) up to MAX_DELAY (30s). Auth-failure close (code 1008) bypasses
 * the schedule entirely — the app should not retry a bad token.
 */

const BASE_DELAY_MS = 1000;
export const MAX_DELAY_MS = 30_000;

/**
 * Compute the delay (ms) before the next reconnect attempt.
 *
 * @param attempts - Number of failed attempts so far (0 = first retry).
 * @returns Delay in milliseconds, capped at {@link MAX_DELAY_MS}.
 *
 * @example
 * nextBackoffDelay(0) // 1000
 * nextBackoffDelay(1) // 2000
 * nextBackoffDelay(2) // 4000
 * nextBackoffDelay(5) // 30000 (capped)
 */
export function nextBackoffDelay(attempts: number): number {
  return Math.min(BASE_DELAY_MS * 2 ** attempts, MAX_DELAY_MS);
}
