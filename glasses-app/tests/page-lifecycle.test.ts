import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { sanitizeContent, decidePageRender } from '../src/lib/page-lifecycle';

const __dirname = dirname(fileURLToPath(import.meta.url));
const MAIN_TS = readFileSync(join(__dirname, '..', 'src', 'main.ts'), 'utf-8');

// ===== D4: empty-content guard =============================================

describe('sanitizeContent', () => {
  it('returns a single space for the empty string', () => {
    expect(sanitizeContent('')).toBe(' ');
  });

  it('returns non-empty strings unchanged', () => {
    expect(sanitizeContent('Connected')).toBe('Connected');
    expect(sanitizeContent('Listening...')).toBe('Listening...');
    expect(sanitizeContent('Error: boom')).toBe('Error: boom');
  });

  it('preserves a single space unchanged', () => {
    expect(sanitizeContent(' ')).toBe(' ');
  });

  it('preserves whitespace-only strings unchanged (non-empty)', () => {
    expect(sanitizeContent('   ')).toBe('   ');
  });
});

// ===== D1: createStartUpPageContainer one-shot decision ====================

describe('decidePageRender', () => {
  describe('when startupRendered is false', () => {
    it('returns first-success when SDK result is 0', () => {
      expect(decidePageRender(false, 0)).toBe('first-success');
    });

    it('returns first-nonsuccess when SDK result is 1 (invalid)', () => {
      expect(decidePageRender(false, 1)).toBe('first-nonsuccess');
    });

    it('returns first-nonsuccess when SDK result is 2 (oversize)', () => {
      expect(decidePageRender(false, 2)).toBe('first-nonsuccess');
    });

    it('returns first-nonsuccess when SDK result is 3 (out of memory)', () => {
      expect(decidePageRender(false, 3)).toBe('first-nonsuccess');
    });
  });

  describe('when startupRendered is true', () => {
    it('returns already-initialized regardless of SDK result', () => {
      expect(decidePageRender(true, 0)).toBe('already-initialized');
      expect(decidePageRender(true, 1)).toBe('already-initialized');
      expect(decidePageRender(true, 2)).toBe('already-initialized');
      expect(decidePageRender(true, 3)).toBe('already-initialized');
    });
  });

  it('the non-success outcomes never authorize a rebuildPageContainer call', () => {
    // The decision type only has three variants; "rebuild" is not among them.
    // This is a structural guarantee — there is no code path from this helper
    // to a destructive rebuild. main.ts must respect it.
    const allOutcomes = new Set<ReturnType<typeof decidePageRender>>([
      decidePageRender(false, 0),
      decidePageRender(false, 1),
      decidePageRender(false, 2),
      decidePageRender(false, 3),
      decidePageRender(true, 0),
      decidePageRender(true, 1),
    ]);
    expect(allOutcomes.has('first-success')).toBe(true);
    expect(allOutcomes.has('first-nonsuccess')).toBe(true);
    expect(allOutcomes.has('already-initialized')).toBe(true);
    // Negative assertion: no 'rebuild' variant exists.
    expect((allOutcomes as Set<string>).has('rebuild')).toBe(false);
  });
});

// ===== Static invariants on main.ts =======================================
// These catch the exact regressions this change fixes: the destructive
// rebuildPageContainer fallback (commit 568252f) and the location.reload()
// call that reset the startupRendered flag (root cause of the cascade).

describe('main.ts static invariants', () => {
  it('declares a module-level startupRendered flag initialized to false', () => {
    expect(MAIN_TS).toMatch(/let\s+startupRendered\s*=\s*false/);
  });

  it('does not import RebuildPageContainer from the SDK', () => {
    expect(MAIN_TS).not.toMatch(/RebuildPageContainer/);
  });

  it('does not call bridge.rebuildPageContainer anywhere', () => {
    expect(MAIN_TS).not.toMatch(/rebuildPageContainer/);
  });

  it('does not call location.reload() anywhere', () => {
    expect(MAIN_TS).not.toMatch(/location\.reload\s*\(/);
  });

  it('imports sanitizeContent from the page-lifecycle helper', () => {
    expect(MAIN_TS).toMatch(/from\s+['"]\.\/lib\/page-lifecycle['"]/);
    expect(MAIN_TS).toMatch(/\bsanitizeContent\b/);
  });

  it('imports decidePageRender from the page-lifecycle helper', () => {
    expect(MAIN_TS).toMatch(/\bdecidePageRender\b/);
  });
});
