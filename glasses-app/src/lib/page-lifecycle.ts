/**
 * Pure helpers for the glasses-app page-container lifecycle.
 *
 * The lifecycle of `createStartUpPageContainer` is governed by Even's SDK
 * contract: it is one-shot per WebView session. This module owns the tiny
 * pure functions that decide what to do given the current flag state and
 * the SDK result code, plus the content-minimum guard for textContainerUpgrade.
 *
 * The mutable `startupRendered` flag itself lives in `main.ts` (module scope);
 * these helpers are pure and unit-testable without a mocked SDK.
 */

/** Minimum-content rule per the `glasses-ui` skill: empty strings become ' '. */
export function sanitizeContent(text: string): string {
  return text.length === 0 ? ' ' : text;
}

/** Decision outcome for `buildPage()` given the current flag + SDK result code. */
export type PageRenderDecision =
  | 'first-success'        // first call, SDK returned success (0)
  | 'first-nonsuccess'     // first call, SDK returned non-success (1/2/3)
  | 'already-initialized'; // flag already set; buildPage() is a no-op

/**
 * Decide what `buildPage()` should do given the current `startupRendered`
 * flag and the SDK's `createStartUpPageContainer` result code.
 *
 * Result codes (per `StartUpPageCreateResult` in the Even Hub SDK):
 *   0 = success, 1 = invalid, 2 = oversize, 3 = out of memory
 *
 * In all cases, the caller sets `startupRendered = true` after the decision:
 *   - `first-success`: the page was created; subsequent calls are no-ops.
 *   - `first-nonsuccess`: the SDK considers the page already created (one-shot
 *     contract); the native layer still owns the prior containers and
 *     `textContainerUpgrade` continues to update them. We MUST NOT fall back
 *     to `rebuildPageContainer` — Even's docs describe it as destructive
 *     ("Full redraw — all state is lost") and commit `0c4d0a6` proved it
 *     blanks real hardware.
 *   - `already-initialized`: `buildPage()` returns immediately without calling
 *     any `bridge.*` method.
 */
export function decidePageRender(
  startupRendered: boolean,
  sdkResult: number,
): PageRenderDecision {
  if (startupRendered) return 'already-initialized';
  return sdkResult === 0 ? 'first-success' : 'first-nonsuccess';
}
