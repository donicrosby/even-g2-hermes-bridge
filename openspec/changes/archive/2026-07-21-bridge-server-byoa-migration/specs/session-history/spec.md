## ADDED Requirements

### Requirement: Server-side conversation history
The bridge server SHALL maintain conversation history server-side because the G2 BYOA client sends only the current user utterance and never sends prior assistant/user turns. History SHALL be keyed by the request client IP address for v1.

#### Scenario: First turn for a client
- **WHEN** a client IP sends its first authenticated BYOA request with content `"My name is Don"`
- **THEN** the server forwards `SYSTEM_PROMPT` plus that user message to LiteLLM, returns the assistant reply, and stores the user message plus assistant reply under that client IP

#### Scenario: Follow-up turn for same client
- **WHEN** the same client IP later sends `"What's my name?"`
- **THEN** the server forwards `SYSTEM_PROMPT`, the stored prior user/assistant turn, and the new user message to LiteLLM

#### Scenario: Different client IP
- **WHEN** a different client IP sends a request
- **THEN** the server does not include history from the first client IP in the forwarded messages

### Requirement: History turn cap
The bridge server SHALL cap stored conversation history to `MAX_HISTORY_TURNS` user/assistant turns per session. When the cap is exceeded, the oldest complete user/assistant turns SHALL be discarded first.

#### Scenario: History exceeds cap
- **WHEN** `MAX_HISTORY_TURNS=2` and a client completes three successful turns
- **THEN** the server stores only the most recent two complete user/assistant turns for that client

### Requirement: Failed turns do not mutate history
The bridge server SHALL update conversation history only after LiteLLM returns a successful assistant reply that is sent back to the glasses. Authentication failures, invalid requests, LiteLLM errors, and canceled duplicate waiters SHALL NOT append to history.

#### Scenario: LiteLLM error
- **WHEN** a client sends a valid BYOA request but LiteLLM returns a non-2xx error
- **THEN** the server returns an error response and leaves the client's prior history unchanged

#### Scenario: Invalid request
- **WHEN** a client sends malformed JSON or no user message
- **THEN** the server returns HTTP 400 and leaves all histories unchanged

### Requirement: Clear history action
The bridge server SHALL support clearing a client's conversation history when the BYOA request body contains `messages[0].content` equal to `/clear` after trimming whitespace and lowercasing. The clear command SHALL return a successful chat-completion response confirming history was cleared and SHALL NOT forward the command to LiteLLM.

#### Scenario: Clear command
- **WHEN** a client sends a valid authenticated request whose latest user content is `/clear`
- **THEN** the server removes all history for that client and returns a chat-completion response containing `Conversation history cleared`

#### Scenario: Clear command for one client
- **WHEN** client A sends `/clear`
- **THEN** client A's history is cleared and client B's history is not changed

### Requirement: History observability
The server SHALL log session history events with client IP, event type, and resulting turn count. The logs SHALL not include full conversation content beyond the first 120 characters of the latest user message.

#### Scenario: History appended
- **WHEN** a successful turn is appended to history
- **THEN** the server logs `history_append` with the client IP and current turn count

#### Scenario: History cleared
- **WHEN** a client clears history
- **THEN** the server logs `history_clear` with the client IP
