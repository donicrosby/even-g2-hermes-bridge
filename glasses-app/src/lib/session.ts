/**
 * Session name rendering helpers for the glasses HUD.
 *
 * Session names from the bridge can be long; the session-name container is a
 * thin strip (~44px) that fits roughly 24 characters. This module owns the
 * truncation policy.
 */

/** Maximum characters rendered in the session-name container. */
export const MAX_SESSION_NAME_LEN = 24;

/**
 * Truncate a session name for HUD display.
 *
 * Names ≤ 24 chars pass through unchanged. Longer names are truncated to
 * 22 chars + ellipsis (total 23 visible chars, leaving room for the cursor
 * indicator on the right edge).
 */
export function truncateSessionName(name: string): string {
  if (name.length <= MAX_SESSION_NAME_LEN) return name;
  return `${name.slice(0, MAX_SESSION_NAME_LEN - 2)}…`;
}
