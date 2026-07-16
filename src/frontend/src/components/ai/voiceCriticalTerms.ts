/**
 * Critical-term detection for voice transcripts (WS5-T7).
 *
 * Adopted policy (2026-07-15): identifiers, fault codes, measurements with
 * units, quantities, negations, and safety terms always require visible
 * confirmation before structured use, and any transcript below the ASR
 * confidence floor (default 0.85, unknown counts as below) is confirmed
 * too. Confirmation produces text input only — never an effect.
 */

export const DEFAULT_CONFIDENCE_FLOOR = 0.85;

export type CriticalTermKind =
  | 'identifier'
  | 'fault_code'
  | 'measurement'
  | 'quantity'
  | 'negation'
  | 'safety';

export interface CriticalSpan {
  start: number;
  end: number;
  text: string;
  kind: CriticalTermKind;
}

const PATTERNS: ReadonlyArray<[CriticalTermKind, RegExp]> = [
  [
    'measurement',
    /\b\d+(?:[.,]\d+)?\s?(?:psi|bar|°?\s?[cf]\b|nm|kv|v|kw|w|a|amps?|volts?|mm|cm|in(?:ch(?:es)?)?|hz|rpm|gpm|lpm|h(?:ours?)?|min(?:utes?)?|%)\b/gi
  ],
  ['quantity', /\b\d+\s?(?:pcs?|pieces?|units?|sets?)\b/gi],
  ['fault_code', /\b[A-Za-z]\d{3,}\b/g],
  ['identifier', /\b[A-Za-z]{2,}[-_]\d{2,}[A-Za-z0-9-]*\b/g],
  [
    'safety',
    /\b(?:loto|lock[\s-]?out|tag[\s-]?out|energi[sz]ed|de-?energi[sz]ed|isolat(?:ed|ion)|pressuri[sz]ed|live|hot\s+work)\b/gi
  ],
  [
    'negation',
    /\b(?:no|not|never|without|off|stopped?|isn't|don't|doesn't|won't|can't)\b/gi
  ]
];

/** Return all critical spans, earliest first, without overlaps. */
export function detectCriticalSpans(text: string): CriticalSpan[] {
  const spans: CriticalSpan[] = [];
  for (const [kind, pattern] of PATTERNS) {
    pattern.lastIndex = 0;
    for (const match of text.matchAll(pattern)) {
      const start = match.index ?? 0;
      const end = start + match[0].length;
      const overlaps = spans.some(
        (span) => start < span.end && end > span.start
      );
      if (!overlaps) {
        spans.push({ start, end, text: match[0], kind });
      }
    }
  }
  return spans.sort((a, b) => a.start - b.start);
}

/** Whether a completed transcript must be confirmed before submission. */
export function needsConfirmation(
  text: string,
  confidence: number | null,
  confidenceFloor: number = DEFAULT_CONFIDENCE_FLOOR
): boolean {
  if (confidence === null || confidence < confidenceFloor) {
    return true;
  }
  return detectCriticalSpans(text).length > 0;
}
