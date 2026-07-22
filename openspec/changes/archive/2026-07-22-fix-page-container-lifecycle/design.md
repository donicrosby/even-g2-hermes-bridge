## Context

The glasses-app runs inside an Even Hub WebView on the user's phone. The Even Hub SDK's page-container API has three layers:

| API | Purpose | Documented behavior |
|---|---|---|
| `createStartUpPageContainer` | One-shot per app session; creates the initial page | Returns `StartUpPageCreateResult`: `0=success`, `1=invalid`, `2=oversize`, `3=outOfMemory`. Per SDK `.d.ts`: *"must be called when starting custom APP, subsequently use rebuildPageContainer to rebuild the page"* |
| `rebuildPageContainer` | Replace the entire page (layout changes) | Per official docs: *"Replace the entire page. Full redraw — all state is lost, brief flicker on hardware."* |
| `textContainerUpgrade` | Flicker-free text content update | Container must already exist; `containerID`/`containerName` must match exactly |

The current glasses-app violates the one-shot contract. `main.ts` calls `createStartUpPageContainer` on every `init()` run. When init runs a second time (e.g., after `location.reload()` from the settings-save handler), the SDK returns non-success and the current code falls back to `rebuildPageContainer`. Empirical evidence (commit `0c4d0a6`) and Even's official docs both confirm this is destructive on real hardware — the BLE redraw fails, the glasses go blank.

### Reference patterns observed

Every public Even Hub app on GitHub uses a module-level `startupRendered` boolean and never calls `location.reload()`:

| Repo | Pattern |
|---|---|
| `BxNxM/even-dev/apps/clock/src/clock-app.ts` | `let startupRendered = false` |
| `BxNxM/even-dev/apps/timer/src/timer-controller.ts` | `state.startupRendered` |
| `BxNxM/even-dev/apps/restapi/src/restapi-app.ts` | `bridgeState.startupRendered` |
| `nickustinov/paddle-even-g2/g2/renderer.ts` | `let startupRendered = false` + parallel `pageSetUp` flag |
| `elizaOS/eliza/plugins/plugin-facewear/src/transport/even-bridge.ts` | class field `this.evenHubStartupCreated` |
| `fabioglimb/even-toolkit/glasses/sdk-wrapper.ts` | class-based Page abstraction |
| `even-realities/evenhub-templates/{asr,image,text-heavy}/src/main.ts` | no flag — single-shot init, never re-enters |

The unifying invariant: **init() runs exactly once per WebView session**, so the flag's lifecycle matches the SDK's one-shot contract perfectly.

## Goals / Non-Goals

**Goals:**
- Stop destroying rendered containers on every init re-entry.
- Make the glasses-app's page lifecycle match the canonical Even Hub pattern.
- Eliminate `location.reload()` as a state-management tool — it is the root cause of the flag-reset bug.
- Survive unexpected WebView reloads (e.g., system-initiated) without going blank.
- Keep the change small, single-package, no protocol changes, no plugin changes.

**Non-Goals:**
- Restoring the `createBridgeQueue` serialization wrapper removed in `5663d77`. That regression is tracked under the existing `glasses-app-bridge-resilience` capability and is a separate change.
- Introducing any *new* legitimate caller of `rebuildPageContainer` (none exist in the current app; this change does not add one).
- Changing the wire protocol, frame schemas, or plugin-side code.
- Packaging hygiene (root `/app.json` cleanup, `.ehpk` filename normalization).
- The prototype QR sideload flow — that's a separate question (likely a dev-server URL/port mismatch, not a code bug).

## Decisions

### D1: Restore the module-level `startupRendered` flag

**Choice.** Add `let startupRendered = false` to `main.ts`'s mutable-state block (alongside `let bridge`, `let ws`, etc.). Gate `buildPage()` on it:

```ts
async function buildPage(): Promise<void> {
  if (!bridge) return;
  if (startupRendered) return;          // already initialized this session

  const containers = { /* ...existing shape... */ };
  const result = await bridge.createStartUpPageContainer(
    new CreateStartUpPageContainer(containers),
  );

  if (result === StartUpPageCreateResult.success) {
    startupRendered = true;
    log.info('createStartUpPageContainer success');
    return;
  }

  // Non-success: the SDK considers the page already created (one-shot).
  // Native layer still owns the prior containers; textContainerUpgrade
  // will keep updating them. Do NOT fall back to rebuildPageContainer —
  // Even's docs say it is destructive ("all state is lost") and
  // commit 0c4d0a6 proved it blanks the glasses on real hardware.
  log.info('createStartUpPageContainer non-success, assuming already initialized', {
    result: Number(result),
  });
  startupRendered = true;               // treat as initialized either way
}
```

**Rationale.** This is the exact pattern used by `BxNxM/even-dev/apps/clock`, `timer`, `restapi`, `nickustinov/paddle-even-g2`, and `elizaOS/eliza`. The flag matches the SDK's one-shot contract 1:1.

**Alternatives considered.**
- **Persist the flag in `setLocalStorage`.** Adds a BLE round-trip to every init. Unnecessary if we also adopt D2 (no more reloads). Kept as a future defensive layer if real-world telemetry shows unexpected WebView reloads.
- **Probe instead of flag (delete the flag entirely, always call `createStartUpPageContainer`, treat non-success as "already initialized").** Rejected for two reasons: (1) it logs a non-success result on every secondary init, which is noisy; (2) on a fresh install where the first call genuinely fails (e.g., oversize), silently treating it as "already initialized" masks a real bug. The flag makes the first-call vs. subsequent-call distinction explicit.

### D2: Replace `location.reload()` with in-page re-initialization

**Choice.** The settings-save click handler currently does:

```ts
saveBtn?.addEventListener('click', () => {
  localStorage.setItem('bridge_url', urlVal);
  localStorage.setItem('bridge_token', tokenVal);
  location.reload();
});
```

Replace with an in-page re-init flow:

```ts
saveBtn?.addEventListener('click', async () => {
  // Validate as before
  if (!urlVal || !tokenVal) { showErr('Both URL and token are required.'); return; }
  if (!urlVal.startsWith('ws://') && !urlVal.startsWith('wss://')) {
    showErr('URL must start with ws:// or wss://'); return;
  }

  localStorage.setItem('bridge_url', urlVal);
  localStorage.setItem('bridge_token', tokenVal);

  // Tear down live state
  form.remove();                          // close config overlay
  if (ws) { ws.close(); ws = null; }
  reconnectAttempts = 0;
  authFailed = false;

  // Refresh container text via flicker-free textContainerUpgrade
  setStatus('Connecting...');
  renderAssistant();
  renderSession();

  // Re-open the WebSocket with the new URL/token
  connect();
});
```

**Rationale.** This is the root-cause fix. The flag-reset bug existed only because `location.reload()` reset module state. With reload gone, the flag survives for the entire app session — matching the canonical pattern.

The page containers are already alive (created during the first init), so `textContainerUpgrade` updates them in place. No `createStartUpPageContainer` re-call, no `rebuildPageContainer`, no flicker, no state loss.

**Alternatives considered.**
- **Keep `location.reload()` and add `sessionStorage` persistence for the flag.** Rejected: `sessionStorage` clears on WebView close, so doesn't survive an actual system-initiated reload; `localStorage` adds BLE round-trip overhead. The reload itself is the smell — every reference Even Hub app avoids it.
- **Hybrid: keep reload, persist flag via SDK `setLocalStorage`.** Rejected for the same reason. Also: SDK storage is itself a BLE call, which means init would need to make a BLE call before deciding whether to make another BLE call — increases cold-start latency.

### D3: Defensive non-success branch (the "C" layer)

**Choice.** Even with D2, an unexpected WebView reload could theoretically happen (e.g., Even Hub host crashes and restarts the WebView, or the system kills the app for memory and restores it). The non-success branch in D1 handles this:

```ts
if (result === StartUpPageCreateResult.success) {
  startupRendered = true;
} else {
  // Native layer still owns prior containers; do nothing destructive.
  log.info('createStartUpPageContainer non-success, assuming already initialized', { ... });
  startupRendered = true;                 // treat as initialized either way
}
```

**Rationale.** This is the safety net. Without it, a future code path that triggers a reload would re-introduce the bug. With it, the worst case is "log a non-success result and continue with the existing containers" — never "destroy the containers and fail to redraw".

**Why `startupRendered = true` even on non-success?** Because the SDK has told us the page is already initialized. Subsequent `textContainerUpgrade` calls will hit living containers. Treating it as `false` would cause `buildPage()` to retry `createStartUpPageContainer` on the next call — which would also return non-success, in a tight loop.

### D4: Guard the empty-string content in `setStatus`

**Choice.** Change `setStatus('')` calls (only one today: `handleToolEnd`) to `setStatus(' ')`.

```ts
function setStatus(text: string): void {
  // Per glasses-ui skill: minimum content is ' ' (single space); empty
  // strings may be silently rejected on real hardware.
  const safe = text.length === 0 ? ' ' : text;
  void upgradeText(STATUS_CID, STATUS_CNAME, safe);
}
```

**Rationale.** Cheap, defensive, and aligns with the `glasses-ui` skill's documented rule. The Image-Based App Pattern section explicitly notes *"single space — required, cannot be empty"*.

**Alternatives considered.** Hunt every caller and force them to send `' '`. Rejected: centralizing the guard in `setStatus` is one line and covers all callers present and future.

## Risks / Trade-offs

- **[Regression: a future `location.reload()` sneaks back in]** → *Mitigation*: the D3 defensive branch makes this safe. Also: a unit test asserts that `buildPage()` never calls `rebuildPageContainer`.
- **[Real hardware behaves differently from the canonical reference apps]** → *Mitigation*: the change restores the exact pattern used by `even-realities/evenhub-templates` (the official Even sample apps). If our hardware differs from the canonical hardware, that's a separate SDK bug to file with Even.
- **[Init fails on first run for a legit reason (e.g., oversize)]** → *Mitigation*: log the non-zero result code at INFO level with the numeric value, so a genuine oversize (`2`) or out-of-memory (`3`) is visible in the phone-side logs panel. The defensive branch is a soft fail, not a silent one.
- **[Settings save flow feels different without a full page refresh]** → *Mitigation*: the user sees the config overlay close, the status row update to "Connecting...", and the WebSocket connect. That's the same visible behavior as before, just without the brief blank flash that reload caused.
- **[No automated test for the BLE-link behavior]** → *Mitigation*: D1 and D2 are pure JS state-machine changes; both are unit-testable with a mocked `bridge` object. The actual BLE behavior is validated by manual smoke test against real hardware (already a step in `plugin/README.md`'s "First-run setup" flow).

## Migration Plan

Single-PR change, scoped to `glasses-app/`. No schema migration, no plugin coordination.

1. Land D4 (`setStatus` guard) — trivial, no behavior change for non-empty strings.
2. Land D1 (restore flag, remove destructive fallback) — the immediate fix. Even if D2 isn't ready, the flag + defensive branch stops the bleeding.
3. Land D2 (replace `location.reload()`) — the root-cause fix. Can be a follow-up commit in the same PR.
4. Manual smoke test on hardware: install fresh, open settings, save, verify glasses don't go blank.
5. Rollback strategy: `git revert <PR>` — no data migration, no persistent state changes (we're *removing* the only persistence-the-flag option from consideration).

## Open Questions

None. The four decisions are independent and self-justifying. Implementation can proceed.
