## ADDED Requirements

### Requirement: Hermes platform plugin registration

The plugin SHALL register `even_g2` as a Hermes platform via `ctx.register_platform(...)` in a `register(ctx)` entry point. Registration SHALL include the adapter factory, env-var requirements (`EVEN_G2_BRIDGE_TOKEN`), platform hint for the system prompt, cron delivery env var (`EVEN_G2_HOME_CHANNEL`), and display metadata (label, emoji).

#### Scenario: Plugin discovered by Hermes

- **WHEN** the user runs `hermes plugins install ./plugin/` on the gateway host
- **THEN** Hermes discovers the plugin's `register(ctx)` entry point and registers the `even_g2` platform

#### Scenario: Plugin enabled after gateway restart

- **WHEN** the user runs `hermes plugins enable even_g2 && hermes gateway restart`
- **THEN** the gateway instantiates `EvenG2Adapter` via the registered factory and the WS server starts listening on the configured port

### Requirement: BasePlatformAdapter interface implementation

The plugin SHALL implement `EvenG2Adapter` inheriting from `BasePlatformAdapter`. The adapter SHALL implement: `connect()`, `disconnect()`, `send_message(chat_id, text)`, `edit_message(chat_id, message_id, text, *, finalize)`, and `get_chat_info(chat_id)`. The adapter SHALL route outbound `send_message` / `edit_message` calls to the corresponding WS connection via the connection registry.

#### Scenario: Gateway sends assistant reply

- **WHEN** the Hermes Gateway calls `adapter.send_message(chat_id="g2-serial-123", text="Hello!")`
- **THEN** the adapter pushes an `assistant.delta` frame with delta="Hello!" to the WS connection registered under `chat_id="g2-serial-123"`

#### Scenario: Gateway streams accumulated text via edit_message

- **WHEN** the gateway calls `adapter.edit_message(chat_id, msg_id, "Hello world")` after previously sending "Hello!"
- **THEN** the adapter diffs "Hello world" against "Hello!" via `StreamState.delta_for()`, pushes an `assistant.delta` frame with delta=" world", and returns `SendResult(success=True, message_id=msg_id)`

### Requirement: Tool-call activity hooks

The plugin SHALL register `pre_tool_call` and `post_tool_call` hooks that emit `tool.start` and `tool.end` frames to the chat_id associated with the running session.

#### Scenario: Agent invokes a tool

- **WHEN** the agent calls a tool named "web_search" during a turn for session S (mapped to chat_id C)
- **THEN** the plugin emits `tool.start` frame with name="web_search" to chat_id C, followed by `tool.end` frame with ok=true when the tool completes

### Requirement: Plugin env-driven configuration

The plugin SHALL read configuration from environment variables: `EVEN_G2_BRIDGE_TOKEN` (required), `EVEN_G2_BRIDGE_HOST` (default `127.0.0.1`), `EVEN_G2_BRIDGE_PORT` (default `8767`), `EVEN_G2_BRIDGE_PUBLIC_URL` (set by Tailscale setup), `EVEN_G2_HOME_CHANNEL` (optional cron delivery), `EVEN_G2_ALLOWED_USERS` (optional ACL), `EVEN_G2_ALLOW_ALL_USERS` (default true for single-user).

#### Scenario: Required token missing

- **WHEN** `EVEN_G2_BRIDGE_TOKEN` is unset and the plugin attempts to connect
- **THEN** the plugin logs a clear error and reports `check_fn` failure to Hermes

#### Scenario: Custom port via env

- **WHEN** `EVEN_G2_BRIDGE_PORT=9999` is set
- **THEN** the WS server binds to port 9999 instead of the default 8767

### Requirement: QR code generator for configuration bootstrap

The plugin SHALL generate a QR code encoding the bridge URL and token as a single-payload URL: `wss://<host>:<port>?token=<token>`. The plugin SHALL render the QR code in three forms:
1. **Terminal ASCII/Unicode** — printed to stdout when the user runs `hermes even-g2 qr`
2. **PNG file** — written to `~/.hermes/even_g2_qr.png`
3. **HTTP endpoint** — served at `GET /qr` on the WS server port (returns `image/png`)

The QR is intended to be scanned by the phone camera (or any QR reader) to populate the glasses-app's bridge URL + token fields without manual entry.

#### Scenario: User runs QR CLI command

- **WHEN** the user runs `hermes even-g2 qr` on the gateway host
- **THEN** the QR code is printed to the terminal as ASCII art and the PNG is written to `~/.hermes/even_g2_qr.png`

#### Scenario: User fetches QR via HTTP

- **WHEN** the user opens `https://<magic-dns>:8443/qr` in a phone browser
- **THEN** the server returns a PNG image of the QR code with `Content-Type: image/png`

#### Scenario: QR payload shape

- **WHEN** the QR code is generated
- **THEN** the encoded payload is `wss://hermes.your-tailnet.ts.net:8443?token=abc123...` (or whatever the configured public URL + token are)
