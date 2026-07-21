// Structured logger for the glasses-app. Emits JSON-shaped objects via
// `console.log`, which the Flutter WebView host captures and surfaces in the
// phone's app log. Use the structured `log.info(event, fields)` form rather
// than free-text `console.log(...)` so logs are machine-parseable.
//
// Example output:
//   {"level":"info","event":"frame","direction":"in","frame_type":"hello.ok",
//    "byte_size":48,"timestamp":"2026-07-21T13:39:28.886Z"}

type LogFields = Record<string, unknown>;

const isDebug = (() => {
  try {
    return localStorage.getItem('bridge_log_level')?.toUpperCase() === 'DEBUG';
  } catch {
    return false;
  }
})();

function emit(level: string, event: string, fields: LogFields): void {
  const entry = {
    level,
    event,
    ...fields,
    timestamp: new Date().toISOString(),
  };
  // eslint-disable-next-line no-console -- glasses-app logs flow through console.log to the Flutter host.
  console.log(JSON.stringify(entry));
}

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
