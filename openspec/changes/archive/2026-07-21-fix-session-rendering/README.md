# fix-session-rendering

Sessions never appear in the glasses-app because the plugin never emits active/sessions frames and hello.ok arrives without an active session id. Wire up the full session round-trip.
