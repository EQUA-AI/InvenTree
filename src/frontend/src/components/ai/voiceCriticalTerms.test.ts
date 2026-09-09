import { describe, expect, it } from 'vitest';

import {
  DEFAULT_CONFIDENCE_FLOOR,
  detectCriticalSpans,
  needsConfirmation
} from './voiceCriticalTerms';

/**
 * Deterministic coverage for the client-side critical-term detector. These
 * pin the CURRENT behaviour (audit baseline 382d4c1e7) so that the Phase A
 * hold-routing change (task A9) is a visible, reviewed diff rather than an
 * accident: today a bare "no" is a critical span, and the exported
 * `needsConfirmation` treats unknown confidence as low (the live hook does
 * not).
 */

describe('detectCriticalSpans', () => {
  it('flags measurements with units', () => {
    const spans = detectCriticalSpans('reading is 50 psi on the gauge');
    expect(spans.map((s) => s.kind)).toEqual(['measurement']);
    expect(spans[0].text.toLowerCase()).toBe('50 psi');
  });

  it('flags quantities, fault codes and identifiers', () => {
    const kinds = detectCriticalSpans(
      'move 12 units of PUMP-104 after fault E4021'
    ).map((s) => s.kind);
    expect(kinds).toEqual(
      expect.arrayContaining(['quantity', 'identifier', 'fault_code'])
    );
  });

  it('flags negation and safety vocabulary', () => {
    const kinds = detectCriticalSpans('the machine is not isolated').map(
      (s) => s.kind
    );
    expect(kinds).toEqual(expect.arrayContaining(['negation', 'safety']));
  });

  it('treats a bare "no" as a negation span (baseline behaviour, changed in A9)', () => {
    const spans = detectCriticalSpans('no');
    expect(spans).toHaveLength(1);
    expect(spans[0].kind).toBe('negation');
  });

  it('returns nothing for ordinary speech', () => {
    expect(detectCriticalSpans('what needs attention today')).toEqual([]);
  });

  it('returns spans sorted by position without overlaps', () => {
    const spans = detectCriticalSpans('50 psi and 12 pcs and no leak');
    const starts = spans.map((s) => s.start);
    expect([...starts].sort((a, b) => a - b)).toEqual(starts);
    for (let i = 1; i < spans.length; i += 1) {
      expect(spans[i].start).toBeGreaterThanOrEqual(spans[i - 1].end);
    }
  });
});

describe('needsConfirmation (exported helper, unused by the live hook)', () => {
  it('holds when confidence is below the floor', () => {
    expect(
      needsConfirmation('hello there', DEFAULT_CONFIDENCE_FLOOR - 0.1)
    ).toBe(true);
  });

  it('holds on unknown confidence (diverges from the hook; reconciled in A9)', () => {
    expect(needsConfirmation('hello there', null)).toBe(true);
  });

  it('does not hold confident, non-critical speech', () => {
    expect(needsConfirmation('hello there', 0.99)).toBe(false);
  });

  it('holds confident speech that contains a critical value', () => {
    expect(needsConfirmation('set it to 50 psi', 0.99)).toBe(true);
  });
});
