## Why

The glasses-app currently destroys its own rendered containers every time it re-initializes. After commit `be85272` removed the module-level `pageCreated` flag, `buildPage()` started calling `createStartUpPageContainer` on every init; commit `568252f` then re-added a fallback to `rebuildPageContainer` when the one-shot `createStartUpPageContainer` returns non-success. Even's official docs describe `rebuildPageContainer` as *"Replace the entire page. Full redraw — all state is lost, brief flicker on hardware"* and commit `0c4d0a6` already proved on real hardware that "the glasses go blank" when this is used as an init-time fallback. The user-visible symptom is that saving the bridge settings triggers `location.reload()`, which re-runs init, which tears down and fails to redraw — perceived as "the app nukes everything."

## What Changes

- **Remove** the destructive `rebuildPageContainer` fallback from `buildPage()` in `glasses-app/src/main.ts`.
- **Restore** the module-level `startupRendered` flag (lost in `be85272`) — matching the canonical pattern used by every public Even Hub reference app (`BxNxM/even-dev`, `nickustinov/paddle-even-g2`, `even-realities/evenhub-templates`, `fabioglimb/even-toolkit`, `elizaOS/eliza`). The flag gates `createStartUpPageContainer` to its one-shot-per-session contract.
- **Replace** `location.reload()` in the settings-save handler with an in-page teardown/rebuild flow that closes the WebSocket, re-runs `restoreState()`/`connect()`, and refreshes container text via `textContainerUpgrade` — without resetting module state. This is the root-cause fix: the canonical pattern works because canonical apps never reload; we make our app canonical.
- **Add a defensive non-success branch** in `buildPage()`: when `createStartUpPageContainer` returns non-success (e.g., an unexpected WebView reload we didn't trigger), log the code and skip rendering entirely. The native layer still owns the prior containers; `textContainerUpgrade` will continue updating them. **Never** fall back to `rebuildPageContainer` from init code paths — Even's docs say it is destructive by design.
- **Guard** the empty-string content case (`setStatus('')` in `handleToolEnd`) to send `' '` (single space) instead, per the `glasses-ui` skill's minimum-content rule.

## Capabilities

### New Capabilities
- `glasses-app-page-lifecycle`: Defines the lifecycle invariants for `createStartUpPageContainer`, `rebuildPageContainer`, and `textContainerUpgrade` — which one is called when, the one-shot rule for startup, and the prohibition against destructive rebuilds from init.

### Modified Capabilities
<!-- None. The existing `glasses-app-bridge-resilience` capability covers per-call timeouts and serialization of bridge calls; this change does not alter those requirements. The new capability is orthogonal. -->

## Impact

**Affected code:**
- `glasses-app/src/main.ts` — `buildPage()`, `init()`, settings-save click handler, `setStatus()`/`handleToolEnd()` empty-string fix.
- `glasses-app/src/lib/state.ts` — add `startupRendered: boolean` to the persisted snapshot (or use a dedicated localStorage key; design.md decides).
- `glasses-app/tests/` — new unit tests for the lifecycle flag persistence and the no-reload settings-save path.

**No API changes**: no frame schema changes, no protocol changes, no plugin changes. Plugin side is unaffected.

**Runtime behavior change**: after this change, the glasses-app stops calling `rebuildPageContainer` from init code paths entirely. The only legitimate callers of `rebuildPageContainer` will be explicit layout-change flows (none exist yet; this change does not introduce any).

**Rollback risk**: low. The change restores a pattern that every public Even Hub reference app on GitHub uses (`BxNxM/even-dev` apps, `nickustinov/paddle-even-g2`, `even-realities/evenhub-templates`, `fabioglimb/even-toolkit`, `elizaOS/eliza` plugin-facewear). It removes a known-destructive code path; no new destructive paths are added.

**Out of scope** (future work, not this change):
- Restoring the `createBridgeQueue` serialization/timeout wrapper removed in `5663d77`. That regression is tracked under the existing `glasses-app-bridge-resilience` capability and is a separate fix.
- The root `/app.json` stale leftover and the three differently-cased `.ehpk` files — packaging hygiene, separate change.
- The `VITE_BRIDGE_*` env vars that `main.ts` ignores — separate cleanup.
