import { describe, expect, it } from 'vitest';

import {
  consentSubmitGate,
  voiceErrorKey,
  voiceErrorPresentation,
  voiceStartFeedback
} from './voiceFeedback';

/*
 * Pure decision tests for VOICE-H01/H02/H05 feedback ownership.
 *
 * H01: error.code is mapped to a stable message key with a generic
 *      fallback; no raw code or server detail may leak into the
 *      presentation object.
 * H02: consent submission keeps the accepted-disclosure companion
 *      checks (voice pair, consent version, idle timeout) and reports
 *      WHY a submit was refused instead of returning silently.
 * H05: a refused start request records why it was refused (eligibility
 *      reason passed through for the existing labels, or the precise
 *      capability gap) instead of silently no-op'ing.
 */

describe('VOICE-H01 error mapping', () => {
  it('maps every known client and server code to a specific key', () => {
    expect(voiceErrorKey('MICROPHONE_DENIED')).toBe('microphone-denied');
    expect(voiceErrorKey('BROWSER_UNSUPPORTED')).toBe('browser-unsupported');
    expect(voiceErrorKey('VOICE_SESSION_EXPIRED')).toBe('session-expired');
    expect(voiceErrorKey('VOICE_SESSION_LIMIT')).toBe('session-limit');
    expect(voiceErrorKey('VOICE_SESSION_FORBIDDEN')).toBe('not-allowed');
    expect(voiceErrorKey('VOICE_CONSENT_REQUIRED')).toBe('consent-required');
    expect(voiceErrorKey('VOICE_LOCALE_UNSUPPORTED')).toBe(
      'locale-unsupported'
    );
    expect(voiceErrorKey('VOICE_SCOPE_CHANGED')).toBe('context-changed');
    expect(voiceErrorKey('VOICE_FOCUS_CHANGED')).toBe('context-changed');
    expect(voiceErrorKey('VOICE_SIGNALING_FAILED')).toBe('connection');
    expect(voiceErrorKey('VOICE_TRANSPORT_UNAVAILABLE')).toBe('connection');
    expect(voiceErrorKey('VOICE_SESSION_UNAVAILABLE')).toBe('connection');
    expect(voiceErrorKey('VOICE_TRANSCRIPT_INCOMPLETE')).toBe('incomplete');
    expect(voiceErrorKey('IDEMPOTENCY_CONFLICT')).toBe('conflict');
    expect(voiceErrorKey('VOICE_SAMPLE_LIMIT')).toBe('sample');
    expect(voiceErrorKey('VOICE_DECISION_CONFLICT')).toBe('generic');
    expect(voiceErrorKey('VOICE_PROMPT_UNKNOWN')).toBe('generic');
  });

  it('falls back to the generic key for unknown codes', () => {
    expect(voiceErrorKey('SOME_FUTURE_SERVER_CODE')).toBe('generic');
    expect(voiceErrorKey('')).toBe('generic');
    expect(voiceErrorKey('undefined')).toBe('generic');
  });

  it('never leaks the raw code or server detail into the presentation', () => {
    const presentation = voiceErrorPresentation({
      code: 'SOME_FUTURE_SERVER_CODE',
      detail: 'raw-server-detail-xyz'
    });
    expect(presentation).toEqual({ key: 'generic' });
    const serialized = JSON.stringify(presentation);
    expect(serialized).not.toContain('SOME_FUTURE_SERVER_CODE');
    expect(serialized).not.toContain('raw-server-detail-xyz');
  });

  it('returns no presentation for a missing error', () => {
    expect(voiceErrorPresentation(null)).toBeNull();
    expect(voiceErrorPresentation(undefined)).toBeNull();
  });
});

describe('VOICE-H02 consent submit gate', () => {
  const capability = {
    voices: [
      { locale: 'en-US', voice: 'aria' },
      { locale: 'de-DE', voice: 'klaus' }
    ],
    consent_version: 'consent-v3-memory',
    idle_timeout_s: 300
  };

  it('accepts a matching voice pair with the exact version and timeout', () => {
    expect(consentSubmitGate(capability, 'de-DE')).toEqual({
      ok: true,
      pair: { locale: 'de-DE', voice: 'klaus' }
    });
  });

  it('refuses a locale with no matching voice pair', () => {
    expect(consentSubmitGate(capability, 'fr-FR')).toEqual({
      ok: false,
      reason: 'missing-voice'
    });
  });

  it('refuses any consent version other than consent-v3-memory', () => {
    expect(
      consentSubmitGate(
        { ...capability, consent_version: 'consent-v2' },
        'en-US'
      )
    ).toEqual({ ok: false, reason: 'consent-version' });
    expect(
      consentSubmitGate({ ...capability, consent_version: undefined }, 'en-US')
    ).toEqual({ ok: false, reason: 'consent-version' });
  });

  it('refuses any idle timeout other than exactly 300 seconds', () => {
    expect(
      consentSubmitGate({ ...capability, idle_timeout_s: 301 }, 'en-US')
    ).toEqual({ ok: false, reason: 'idle-timeout' });
    expect(
      consentSubmitGate({ ...capability, idle_timeout_s: 299 }, 'en-US')
    ).toEqual({ ok: false, reason: 'idle-timeout' });
    expect(
      consentSubmitGate({ ...capability, idle_timeout_s: undefined }, 'en-US')
    ).toEqual({ ok: false, reason: 'idle-timeout' });
  });

  it('refuses when capability metadata is missing entirely', () => {
    expect(consentSubmitGate(null, 'en-US')).toEqual({
      ok: false,
      reason: 'missing-voice'
    });
    expect(consentSubmitGate(undefined, 'en-US')).toEqual({
      ok: false,
      reason: 'missing-voice'
    });
  });
});

describe('VOICE-H05 start refusal feedback', () => {
  it('records the eligibility reason so existing labels can render it', () => {
    expect(
      voiceStartFeedback({
        startBlockedReason: 'turn-in-flight',
        capability: { enabled: true }
      })
    ).toEqual({ key: 'blocked', reason: 'turn-in-flight' });
  });

  it('records an unknown eligibility reason verbatim for the safe fallback', () => {
    expect(
      voiceStartFeedback({
        startBlockedReason: 'something-new',
        capability: { enabled: true }
      })
    ).toEqual({ key: 'blocked', reason: 'something-new' });
  });

  it('records which capability gap blocked the start', () => {
    expect(
      voiceStartFeedback({ startBlockedReason: null, capability: null })
    ).toEqual({ key: 'capability-missing' });
    expect(
      voiceStartFeedback({
        startBlockedReason: null,
        capability: { enabled: false }
      })
    ).toEqual({ key: 'capability-disabled' });
    expect(
      voiceStartFeedback({
        startBlockedReason: null,
        capability: { enabled: true, runtime: { available: false } }
      })
    ).toEqual({ key: 'runtime-unavailable' });
    // Gate semantics preserved verbatim: only runtime.available === false
    // blocks in requestStart; a permanently_failed runtime without that flag
    // still proceeds and is refused later by the controller, so no feedback
    // is recorded here.
    expect(
      voiceStartFeedback({
        startBlockedReason: null,
        capability: {
          enabled: true,
          runtime: { available: false, state: 'permanently_failed' }
        }
      })
    ).toEqual({ key: 'runtime-unavailable' });
    expect(
      voiceStartFeedback({
        startBlockedReason: null,
        capability: { enabled: true, runtime: { state: 'permanently_failed' } }
      })
    ).toBeNull();
  });

  it('records nothing when the start request may proceed', () => {
    expect(
      voiceStartFeedback({
        startBlockedReason: null,
        capability: { enabled: true }
      })
    ).toBeNull();
    expect(
      voiceStartFeedback({
        startBlockedReason: null,
        capability: { enabled: true, runtime: { available: true } }
      })
    ).toBeNull();
    expect(
      voiceStartFeedback({ startBlockedReason: null, capability: undefined })
    ).toEqual({ key: 'capability-missing' });
  });
});
