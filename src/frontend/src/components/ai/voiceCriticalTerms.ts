/**
 * Critical-term detection and the client-side transcript-review hold policy.
 *
 * Policy (2026-07-15, amended by the voice-UX plan A9 on 2026-09-09):
 * identifiers, fault codes, measurements with units, quantities, negations,
 * and safety terms hold a transcript for review before structured use, and
 * any transcript measurably below the ASR confidence floor (default 0.85) is
 * held too. Unknown confidence does NOT hold: providers may omit it and
 * holding every utterance would kill the hands-free loop. A bare decision
 * utterance ("no", "cancel", "confirm", "stop speaking") is never held: it is
 * an answer to something, and the server decides what. Review produces text
 * input only, never an effect.
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

/**
 * Whole-utterance decision vocabulary for the transcript-review hold. These
 * are the ONLY words the client interprets itself; everything else is
 * forwarded to the server, which owns action decisions.
 */
export const VOICE_CONFIRM_RE =
  /^(?:confirm|yes|yes please|continue|send it|submit|go ahead|that's right|correct)$/;
export const VOICE_DISCARD_RE =
  /^(?:discard|cancel|no|nope|scratch that|start over|delete|discard it|try again)$/;
export const VOICE_STOP_RE =
  /^(?:stop|stop speaking|stop talking|be quiet|quiet|stop listening|end voice)$/;

/** Lower-case, trim, and drop trailing punctuation for decision matching. */
export function normalizeDecisionUtterance(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[.!?,]+$/g, '')
    .trim();
}

/** True when the whole utterance is a decision word (never held for review). */
export function isBareDecisionUtterance(text: string): boolean {
  const decision = normalizeDecisionUtterance(text);
  return (
    VOICE_CONFIRM_RE.test(decision) ||
    VOICE_DISCARD_RE.test(decision) ||
    VOICE_STOP_RE.test(decision)
  );
}

const IDENTIFIER_NUMBER_WORDS = new Set(
  'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand and'.split(
    ' '
  )
);

/** Route an explicit target correction to the active server decision first. */
export function isVoiceTargetCorrection(text: string): boolean {
  const match =
    /^(?:no[, ]+)?(?:i meant|make that)\s+(?:(?:work\s*order|wo)\s+)?([a-z0-9, -]{1,121})$/i.exec(
      normalizeDecisionUtterance(text)
    );
  if (!match) return false;
  const reference = match[1].trim();
  if (
    /^[a-z]*[- ]?\d+$/i.test(reference) ||
    /^[1-9]\d{0,2}(?:,\d{3}){1,2}$/.test(reference)
  )
    return true;
  const words = reference.toLowerCase().split(/[ -]+/);
  // This routes, not interprets: only the backend validates number grammar,
  // resolves actor scope and creates a fresh labelled preview.
  return (
    words.length <= 16 &&
    words.every((word) => IDENTIFIER_NUMBER_WORDS.has(word))
  );
}

/**
 * Whether a completed transcript must be held for review before submission.
 * This is the live hook's policy: a bare decision word is forwarded, a
 * measurably low confidence holds, unknown confidence does not, and critical
 * spans hold.
 */
export function shouldHoldTranscript(
  text: string,
  confidence: number | null,
  confidenceFloor: number = DEFAULT_CONFIDENCE_FLOOR
): boolean {
  if (isBareDecisionUtterance(text)) {
    return false;
  }
  if (typeof confidence === 'number' && confidence < confidenceFloor) {
    return true;
  }
  return detectCriticalSpans(text).length > 0;
}
