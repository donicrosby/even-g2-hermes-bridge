import { describe, it, expect } from 'vitest';
import { truncateSessionName, MAX_SESSION_NAME_LEN } from '../src/lib/session';

describe('truncateSessionName', () => {
  it('passes through names at or under the limit', () => {
    expect(truncateSessionName('short')).toBe('short');
    expect(truncateSessionName('a')).toBe('a');
    expect(truncateSessionName('')).toBe('');
  });

  it('passes through a name exactly at the limit', () => {
    const exact = 'a'.repeat(MAX_SESSION_NAME_LEN);
    expect(truncateSessionName(exact)).toBe(exact);
  });

  it('truncates names over the limit with an ellipsis', () => {
    const long = 'a'.repeat(MAX_SESSION_NAME_LEN + 1);
    const result = truncateSessionName(long);
    expect(result.length).toBe(MAX_SESSION_NAME_LEN - 1);
    expect(result.endsWith('…')).toBe(true);
  });

  it('truncates very long names to the same length', () => {
    const veryLong = 'session-'.repeat(20);
    const result = truncateSessionName(veryLong);
    expect(result.length).toBe(MAX_SESSION_NAME_LEN - 1);
    expect(result.endsWith('…')).toBe(true);
  });

  it('preserves the first portion of the name', () => {
    const result = truncateSessionName('Introductory Conversation About Important Matters');
    expect(result.startsWith('Introductory Conversat')).toBe(true);
    expect(result).toBe('Introductory Conversat…');
  });

  it('handles unicode names', () => {
    const result = truncateSessionName('שיחה על דברים חשובים מאוד באמת');
    expect(result.endsWith('…')).toBe(true);
  });
});
