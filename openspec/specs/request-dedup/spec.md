# request-dedup

## Purpose

Defines how the `bridge-server/` coalesces duplicate parallel BYOA requests from a single client so the upstream LiteLLM is not multiply-charged for the same utterance. Covers in-flight coalescing, recent-result caching, history-append safety, observability, and configuration.

## Requirements

### Requirement: Duplicate request coalescing
The bridge server SHALL coalesce duplicate parallel BYOA requests from the same client IP with the same latest user message content. Duplicate detection SHALL use a deduplication key derived from client IP and a SHA-256 hash of the latest user content.

#### Scenario: Parallel duplicate requests
- **WHEN** two authenticated requests arrive concurrently from the same client IP with the same latest user message content
- **THEN** the server sends only one LiteLLM request and both HTTP callers receive the same chat-completion response

#### Scenario: Same content from different clients
- **WHEN** two different client IPs send the same latest user message content concurrently
- **THEN** the server treats them as distinct requests and sends separate LiteLLM requests

### Requirement: Recent-result cache
The bridge server SHALL cache completed responses for `DEDUP_WINDOW_SECONDS` after completion. An identical request from the same client IP within that window SHALL return the cached response without calling LiteLLM.

#### Scenario: Duplicate arrives after first completes
- **WHEN** a client sends a request and then sends the exact same latest user message again within `DEDUP_WINDOW_SECONDS`
- **THEN** the second request returns the cached response and does not call LiteLLM

#### Scenario: Duplicate arrives after window expires
- **WHEN** a client sends the exact same latest user message after `DEDUP_WINDOW_SECONDS` has elapsed
- **THEN** the server treats it as a new utterance and calls LiteLLM again

### Requirement: Deduplication does not double-append history
When duplicate requests share one LiteLLM result, the bridge server SHALL append the corresponding user/assistant turn to session history exactly once.

#### Scenario: Parallel duplicate successful turn
- **WHEN** two duplicate parallel requests share one successful LiteLLM result
- **THEN** history for that client gains exactly one user message and one assistant message

#### Scenario: Cached duplicate after successful turn
- **WHEN** a duplicate request receives a recent cached response
- **THEN** history is not appended again

### Requirement: Deduplication observability
The bridge server SHALL log whether each request is a new in-flight request, an in-flight dedup hit, a recent-cache hit, or an expired/new request. Logs SHALL include client IP, dedup key prefix, and current cache age when applicable.

#### Scenario: In-flight dedup hit
- **WHEN** a duplicate request awaits an existing in-flight LiteLLM task
- **THEN** the server logs `dedup_inflight_hit` with client IP and dedup key prefix

#### Scenario: Recent cache hit
- **WHEN** a duplicate request returns a completed cached response
- **THEN** the server logs `dedup_recent_hit` with client IP, dedup key prefix, and cache age

### Requirement: Deduplication settings
The deduplication window SHALL be configurable via `DEDUP_WINDOW_SECONDS` and SHALL default to 5 seconds. Values less than 1 second SHALL be rejected at startup as invalid configuration.

#### Scenario: Default window
- **WHEN** `DEDUP_WINDOW_SECONDS` is unset
- **THEN** the server uses a 5-second recent-result window

#### Scenario: Invalid window
- **WHEN** `DEDUP_WINDOW_SECONDS=0`
- **THEN** the server refuses to start with a clear configuration error
