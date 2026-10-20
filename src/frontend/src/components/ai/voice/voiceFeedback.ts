/**
 * Pure feedback decision helpers for voice (VOICE-H01/H02/H05).
 *
 * This module is deliberately free of Lingui, DOM and React so it can be
 * verified with the pure Node test runner (see voiceFeedback.test.ts). The
 * translated strings for each key live next to the surfaces that render them
 * (VoiceComposerControl.tsx / VoiceSessionControl.tsx / VoiceConsentDialog.tsx)
 * using the Lingui `t` macro.
 *
 * What changed and why (owner-authorized VOICE-H01/H02/H05):
 * - H01: a raw `error.code` is never rendered; surfaces map it to a stable
 *   message key with a generic fallback. The server `detail` string is
 *   intentionally NOT part of any presentation object — no raw server detail
 *   can be disclosed by construction.
 * - H02: consent submission keeps the exact existing checks (voice pair,
 *   `consent-v3-memory`, `idle_timeout_s === 300`) and reports the failing
 *   check instead of returning silently. The gate is never loosened.
 * - H05: a refused start request records why it was refused so surfaces can
 *   render an accessible explanation (existing eligibility labels, or a safe
 *   fallback for unknown reasons). The proceed condition is byte-for-byte the
 *   pre-existing requestStart gate — feedback ownership only.
 */

export type VoiceErrorKey =
  | 'microphone-denied'
  | 'browser-unsupported'
  | 'session-expired'
  | 'session-limit'
  | 'not-allowed'
  | 'connection'
  | 'consent-required'
  | 'locale-unsupported'
  | 'context-changed'
  | 'incomplete'
  | 'conflict'
  | 'sample'
  | 'generic';

const ERROR_KEYS: Record<string, VoiceErrorKey> = {
  MICROPHONE_DENIED: 'microphone-denied',
  BROWSER_UNSUPPORTED: 'browser-unsupported',
  VOICE_SESSION_EXPIRED: 'session-expired',
  VOICE_SESSION_LIMIT: 'session-limit',
  VOICE_SESSION_FORBIDDEN: 'not-allowed',
  VOICE_CONSENT_REQUIRED: 'consent-required',
  VOICE_LOCALE_UNSUPPORTED: 'locale-unsupported',
  VOICE_SCOPE_CHANGED: 'context-changed',
  VOICE_FOCUS_CHANGED: 'context-changed',
  VOICE_SIGNALING_FAILED: 'connection',
  VOICE_TRANSPORT_UNAVAILABLE: 'connection',
  VOICE_SESSION_UNAVAILABLE: 'connection',
  VOICE_TIMING_UNAVAILABLE: 'connection',
  VOICE_TRANSCRIPT_INCOMPLETE: 'incomplete',
  VOICE_RESPONSE_INCOMPLETE: 'incomplete',
  IDEMPOTENCY_CONFLICT: 'conflict',
  VOICE_SAMPLE_UNAVAILABLE: 'sample',
  VOICE_SAMPLE_LIMIT: 'sample'
};

/**
 * Map a raw error code to its stable message key. Unknown (or missing) codes
 * get the generic fallback — the raw code is never echoed to the user.
 */
export function voiceErrorKey(code: string | null | undefined): VoiceErrorKey {
  return (code && ERROR_KEYS[code]) || 'generic';
}

export interface VoiceErrorPresentation {
  key: VoiceErrorKey;
}

/**
 * Presentation for a voice error: only the message key. `code` and `detail`
 * stay out of the result so raw server detail cannot leak into any surface.
 */
export function voiceErrorPresentation(
  error: { code: string; detail?: string } | null | undefined
): VoiceErrorPresentation | null {
  if (!error) return null;
  return { key: voiceErrorKey(error.code) };
}

export interface VoicePair {
  locale: string;
  voice: string;
}

export interface ConsentGateCapability {
  voices?: VoicePair[];
  consent_version?: string;
  idle_timeout_s?: number;
}

export type ConsentGateOutcome =
  | { ok: true; pair: VoicePair }
  | {
      ok: false;
      reason: 'missing-voice' | 'consent-version' | 'idle-timeout';
    };

/**
 * VOICE-H02: the exact, preserved consent-submit gate. Every pre-existing
 * check remains authoritative (pair must exist, consent_version must be
 * exactly 'consent-v3-memory', idle_timeout_s must be exactly 300); the only
 * change is that the failing check is reported instead of returning silently.
 */
export function consentSubmitGate(
  capability: ConsentGateCapability | null | undefined,
  locale: string
): ConsentGateOutcome {
  const pair = capability?.voices?.find((p) => p.locale === locale);
  if (!pair) return { ok: false, reason: 'missing-voice' };
  if (capability?.consent_version !== 'consent-v3-memory')
    return { ok: false, reason: 'consent-version' };
  if (capability?.idle_timeout_s !== 300)
    return { ok: false, reason: 'idle-timeout' };
  return { ok: true, pair };
}

export type VoiceStartFeedback =
  | { key: 'blocked'; reason: string }
  | { key: 'capability-missing' }
  | { key: 'capability-disabled' }
  | { key: 'runtime-unavailable' };

export interface StartFeedbackCapability {
  enabled?: boolean;
  runtime?: { available?: boolean; state?: string };
}

/**
 * VOICE-H05: decide whether a start request may proceed, and if not, why.
 * The proceed condition is exactly the pre-existing requestStart gate
 * (`capability?.enabled && capability.runtime?.available !== false`) — this
 * helper adds feedback ownership only and never changes eligibility.
 */
export function voiceStartFeedback(input: {
  startBlockedReason: string | null;
  capability: StartFeedbackCapability | null | undefined;
}): VoiceStartFeedback | null {
  if (input.startBlockedReason)
    return { key: 'blocked', reason: input.startBlockedReason };
  const capability = input.capability;
  if (capability?.enabled && capability.runtime?.available !== false)
    return null;
  if (capability == null) return { key: 'capability-missing' };
  if (!capability.enabled) return { key: 'capability-disabled' };
  return { key: 'runtime-unavailable' };
}
