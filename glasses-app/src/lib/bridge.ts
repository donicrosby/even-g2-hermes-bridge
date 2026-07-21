const BRIDGE_TIMEOUT_MS = 4000;

export class BridgeTimeoutError extends Error {
  constructor(ms: number) {
    super(`bridge call timed out after ${ms}ms`);
    this.name = 'BridgeTimeoutError';
  }
}

export function withBridgeTimeout<T>(p: Promise<T>, ms: number = BRIDGE_TIMEOUT_MS): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      console.warn(`[Hermes] bridge call timed out after ${ms}ms`);
      reject(new BridgeTimeoutError(ms));
    }, ms);
  });
  return Promise.race([p, timeout]).finally(() => {
    if (timer !== undefined) clearTimeout(timer);
  });
}

export interface BridgeQueue {
  runBridge: <T>(label: string, thunk: () => Promise<T>) => Promise<T | undefined>;
  serializedBridge: <T>(thunk: () => Promise<T>) => Promise<T>;
  reset: () => void;
}

export function createBridgeQueue(): BridgeQueue {
  let chain: Promise<unknown> = Promise.resolve();

  function serializedBridge<T>(thunk: () => Promise<T>): Promise<T> {
    // .then(thunk, thunk) so a prior failure doesn't skip the next call.
    const result = chain.then<T, T>(thunk, thunk);
    chain = result.catch(() => undefined);
    return result;
  }

  function runBridge<T>(label: string, thunk: () => Promise<T>): Promise<T | undefined> {
    return serializedBridge(() => withBridgeTimeout(thunk())).then(
      (v) => v,
      (e) => {
        console.warn(`[Hermes] ${label} failed:`, e);
        return undefined;
      },
    );
  }

  function reset(): void {
    chain = Promise.resolve();
  }

  return { runBridge, serializedBridge, reset };
}
