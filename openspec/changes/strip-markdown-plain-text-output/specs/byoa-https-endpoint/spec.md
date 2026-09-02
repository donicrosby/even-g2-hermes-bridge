## MODIFIED Requirements

### Requirement: BYOA HTTPS endpoint accepts OpenAI/OpenClaw chat-completion requests

The plugin SHALL expose an HTTP `POST` endpoint at `/v1/chat/completions` on the same port as the WS server (default 8767), served via the existing `BridgeServer.process_request` multiplexing hook. The endpoint SHALL accept requests with `Content-Type: application` — *(content preserved from existing spec; unchanged except as noted)* — and the chat-completion response `choices[0].message.content` SHALL be markdown-stripped plain text per the `plain-text-output` capability.

#### Scenario: Response content is plain text

- **WHEN** a valid BYOA request completes and the model produced markdown (`**bold**`, backticks, headers)
- **THEN** `choices[0].message.content` SHALL contain no markdown emphasis markers, fences, or header markers

#### Scenario: Valid BYOA request with fast LLM response

(preserved from existing spec — unchanged)

#### Scenario: Missing user message in request body

(preserved from existing spec — unchanged)
