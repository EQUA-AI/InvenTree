import { describe, expect, it } from 'vitest';

import {
  DEFAULT_CONFIDENCE_FLOOR,
  detectCriticalSpans,
  isBareDecisionUtterance,
  normalizeDecisionUtterance,
  shouldHoldTranscript
} from './voiceCriticalTerms';

/**
 * Deterministic coverage for the client-side critical-term detector and the
 * transcript-review hold policy (voice-UX plan A9): a bare decision word is
 * never held, unknown confidence never holds, critical spans and measurably
 * low confidence do.
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

  it('still detects a bare "no" as a negation span (the hold policy decides)', () => {
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

describe('decision vocabulary', () => {
  it('normalizes case and trailing punctuation', () => {
    expect(normalizeDecisionUtterance('  Confirm. ')).toBe('confirm');
    expect(normalizeDecisionUtterance('No!')).toBe('no');
  });

  it('recognises bare confirm, discard and stop utterances', () => {
    for (const text of [
      'yes',
      'Confirm.',
      'go ahead',
      'no',
      'cancel',
      'scratch that',
      'stop speaking',
      'stop'
    ]) {
      expect(isBareDecisionUtterance(text)).toBe(true);
    }
  });

  it('does not treat sentences containing decision words as decisions', () => {
    for (const text of [
      'no leak found',
      'cancel the order for pump seals',
      'stop the pump'
    ]) {
      expect(isBareDecisionUtterance(text)).toBe(false);
    }
  });
});

describe('shouldHoldTranscript', () => {
  it('never holds a bare decision word', () => {
    expect(shouldHoldTranscript('no', null)).toBe(false);
    expect(shouldHoldTranscript('cancel', 0.2)).toBe(false);
    expect(shouldHoldTranscript('confirm', null)).toBe(false);
  });

  it('holds critical content and measurably low confidence', () => {
    expect(shouldHoldTranscript('the machine is not isolated', null)).toBe(
      true
    );
    expect(shouldHoldTranscript('fifteen not fifty', null)).toBe(true);
    expect(shouldHoldTranscript('set it to 50 psi', 0.99)).toBe(true);
    expect(
      shouldHoldTranscript('hello there', DEFAULT_CONFIDENCE_FLOOR - 0.1)
    ).toBe(true);
  });

  it('does not hold ordinary confident speech or unknown confidence', () => {
    expect(shouldHoldTranscript('hello there', 0.99)).toBe(false);
    expect(shouldHoldTranscript('hello there', null)).toBe(false);
  });
});
