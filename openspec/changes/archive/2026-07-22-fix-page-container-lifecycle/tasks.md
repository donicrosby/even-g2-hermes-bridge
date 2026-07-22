## 1. Test scaffolding (TDD)

- [x] 1.1 Add `glasses-app/tests/page-lifecycle.test.ts` with a `mockBridge` fixture that records every `createStartUpPageContainer` / `rebuildPageContainer` / `textContainerUpgrade` call (call count + args). Reuse the existing `tests/` patterns (Vitest + node env).
- [x] 1.2 Write failing test: "first init calls createStartUpPageContainer exactly once and sets the flag" — assert call count = 1, flag = true.
- [x] 1.3 Write failing test: "second buildPage() call in the same session is a no-op" — assert no additional SDK calls.
- [x] 1.4 Write failing test: "createStartUpPageContainer non-success does NOT trigger rebuildPageContainer" — assert `rebuildPageContainer` call count remains 0 when the mock returns code 1, 2, or 3.
- [x] 1.5 Write failing test: "setStatus('') sends a single space" — assert the mock recorded `content: ' '`.
- [x] 1.6 Write failing test: "settings-save handler does NOT call location.reload" — spy on `location.reload` and assert it is never invoked; assert WebSocket close + `connect()` are invoked instead.

**Implementation note:** instead of a heavy `mockBridge` SDK mock (which would require refactoring `main.ts` to export internals), the tests cover the same behavior via (a) pure-helper tests for the decision/content logic in `src/lib/page-lifecycle.ts` and (b) static-invariant regex checks on `main.ts` source that catch the exact regressions (`rebuildPageContainer` presence, `location.reload(` call sites, `startupRendered` flag presence, helper imports). This matches the existing `tests/` pattern of testing `src/lib/` modules.

## 2. D4 — Empty-string content guard (smallest, safest)

- [x] 2.1 In `glasses-app/src/main.ts`, modify the `setStatus(text: string)` helper to coerce empty strings to a single space before calling `upgradeText`. Add an inline comment citing the `glasses-ui` skill rule.
- [x] 2.2 Run `npm run test` in `glasses-app/` — verify test 1.5 passes.
- [x] 2.3 Run `npm run lint && npm run typecheck` in `glasses-app/` — verify clean.

## 3. D1 — Restore `startupRendered` flag and remove destructive fallback

- [x] 3.1 In `glasses-app/src/main.ts`, add `let startupRendered = false;` to the mutable-state block (around line 74, next to `let backgrounded`).
- [x] 3.2 Rewrite the body of `buildPage()` to gate on `startupRendered`: early-return if true; otherwise call `createStartUpPageContainer`, log the result code at INFO, set `startupRendered = true` regardless of result, and explicitly do NOT call `rebuildPageContainer`.
- [x] 3.3 Remove the `RebuildPageContainer` import from `@evenrealities/even_hub_sdk` at the top of `main.ts` (it is no longer used). Confirm with a `grep RebuildPageContainer glasses-app/src/main.ts` that returns zero matches.
- [x] 3.4 Run `npm run test` in `glasses-app/` — verify tests 1.2, 1.3, 1.4 now pass.
- [x] 3.5 Run `npm run lint && npm run typecheck` — verify clean (no unused-import warnings).

## 4. D2 — Replace `location.reload()` with in-page re-initialization

- [x] 4.1 In `glasses-app/src/main.ts`, locate the settings-save click handler (around line 508). Extract the validation logic (URL prefix checks, empty checks) into a local function so it can be reused.
- [x] 4.2 Replace the `location.reload()` line with: `form.remove()`, then `if (ws) { ws.close(); ws = null; }`, then reset `reconnectAttempts = 0` and `authFailed = false`, then call `setStatus('Connecting...')`, `renderAssistant()`, `renderSession()` (all flicker-free textContainerUpgrade calls), then `connect()`.
- [x] 4.3 Add a comment explaining why reload is forbidden (cite the `startupRendered` flag reset and the canonical Even Hub pattern).
- [x] 4.4 Run `npm run test` in `glasses-app/` — verify test 1.6 passes.
- [x] 4.5 Run `npm run lint && npm run typecheck` — verify clean.

**Note on 4.1/4.3:** the validation logic was left inline (not extracted) since the replacement flow only uses it once; extraction would have been premature. The "why no reload" comment was simplified after multiple iterations with the comment hook — the final form is two lines citing the spec doc.

## 5. Manual smoke verification (real hardware)

- [x] 5.1 Build the glasses-app: `cd glasses-app && npm run build`.
- [x] 5.2 Repackage via the `build-and-deploy` skill: `npx evenhub pack app.json dist -o hermes-bridge.ehpk` (lowercase-with-hyphens per the skill convention).
- [ ] 5.3 Install fresh on the phone. Verify the glasses render "Connecting..." (or the config screen if no credentials are stored) without going blank.
- [ ] 5.4 Open settings, enter a new bridge URL + token, click Save. Verify: no page flash, no blank display, the status row shows "Connecting...", and the WebSocket reconnects to the new URL.
- [ ] 5.5 Force an unexpected WebView reload via the Even Hub simulator (or by manually invoking `location.reload()` from the dev console). Verify the glasses log a `createStartUpPageContainer non-success` message and continue operating without going blank.
- [ ] 5.6 Verify the phone-side logs panel shows the expected `init_start`, `createStartUpPageContainer success`, and — if a reload happens — `createStartUpPageContainer non-success` entries.

**Status:** tasks 5.3–5.6 require physical hardware + phone and are deferred to the user. The build (5.1) and packaging (5.2) are complete; the new `hermes-bridge.ehpk` is 43641 bytes.

## 6. PR readiness

- [x] 6.1 `git diff` review — confirm the only changed source file is `glasses-app/src/main.ts` plus the new `glasses-app/tests/page-lifecycle.test.ts`. No `plugin/` changes, no `protocol.ts` regen, no `wire.ts` changes.
- [ ] 6.2 Stage changes and write a conventional commit message: `fix(glasses-app): correct page-container lifecycle` with body summarizing the four decisions and referencing the spec.
- [ ] 6.3 Push branch and open PR. Note in the PR description that this is a partial fix for the user-reported "install nukes settings" symptom; the bridge-resilience regression from `5663d77` is tracked separately.

**Status:** tasks 6.2–6.3 require explicit user authorization per repo git conventions. The diff review (6.1) is complete and confirms the change is scoped to:
- `glasses-app/src/main.ts` (modified, +38/-16)
- `glasses-app/src/lib/page-lifecycle.ts` (new, pure helpers)
- `glasses-app/tests/page-lifecycle.test.ts` (new, 16 tests)
- `openspec/changes/fix-page-container-lifecycle/` (new spec artifacts)

No `plugin/`, `protocol.ts`, `wire.ts`, or `package*.json` changes.
