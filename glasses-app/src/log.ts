// Structured logger for the glasses-app. Emits JSON-shaped objects via
// `console.log`, which the Flutter WebView host captures and surfaces in the
// phone's app log. Also maintains a ring buffer for the phone-side logs panel.

type LogFields = Record<string, unknown>;

const MAX_ENTRIES = 200;
const STORAGE_KEY = 'hermes_log_buffer';
const logBuffer: string[] = [];

try {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored) {
    const parsed: unknown = JSON.parse(stored);
    if (Array.isArray(parsed)) logBuffer.push(...parsed.filter((e): e is string => typeof e === 'string'));
  }
} catch { /* ignore */ }

function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(logBuffer));
  } catch { /* ignore quota errors */ }
}

function emit(level: string, event: string, fields: LogFields): void {
  const entry = {
    level,
    event,
    ...fields,
    timestamp: new Date().toISOString(),
  };
  const line = JSON.stringify(entry);
  // eslint-disable-next-line no-console -- glasses-app logs flow through console.log to the Flutter host.
  console.log(line);
  logBuffer.push(line);
  if (logBuffer.length > MAX_ENTRIES) logBuffer.shift();
  persist();
}

export function getLogBuffer(): string[] {
  return [...logBuffer];
}

export function clearLogBuffer(): void {
  logBuffer.length = 0;
}

// Catch uncaught errors and unhandled promise rejections so they appear
// in the logs panel instead of silently crashing the WebView.
if (typeof window !== 'undefined') {
  window.addEventListener('error', (e) => {
    emit('error', 'uncaught_error', {
      message: e.message,
      filename: e.filename,
      line: e.lineno,
      col: e.colno,
      error: e.error instanceof Error ? e.error.stack : String(e.error),
    });
  });
  window.addEventListener('unhandledrejection', (e) => {
    emit('error', 'unhandled_rejection', {
      reason: e.reason instanceof Error ? e.reason.stack : String(e.reason),
    });
  });
}

const isDebug = (() => {
  try {
    return localStorage.getItem('bridge_log_level')?.toUpperCase() === 'DEBUG';
  } catch {
    return false;
  }
})();

export const log = {
  debug(event: string, fields: LogFields = {}): void {
    if (!isDebug) return;
    emit('debug', event, fields);
  },
  info(event: string, fields: LogFields = {}): void {
    emit('info', event, fields);
  },
  warn(event: string, fields: LogFields = {}): void {
    emit('warn', event, fields);
  },
  error(event: string, fields: LogFields = {}): void {
    emit('error', event, fields);
  },
};
