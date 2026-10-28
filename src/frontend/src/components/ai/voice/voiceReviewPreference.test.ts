import { describe, expect, it } from 'vitest';
import {
  DEFAULT_VOICE_REVIEW_EVERY_REQUEST,
  mergeVoicePreferences,
  normalizeVoiceReviewEveryRequest
} from './types';

describe('review every request preference', () => {
  it('keeps the existing selective review default and accepts only explicit boolean opt-in', () => {
    expect(DEFAULT_VOICE_REVIEW_EVERY_REQUEST).toBe(false);
    expect(normalizeVoiceReviewEveryRequest(true)).toBe(true);
    for (const value of [undefined, null, false, 'true', 'false', 1, {}, []]) {
      expect(normalizeVoiceReviewEveryRequest(value)).toBe(false);
    }
  });
  it('restores a selected boolean without changing independent voice or host preferences', () => {
    const current = {
      voiceEarcons: true,
      voiceSrAnnounceTranscripts: false,
      host: 'local'
    };
    for (const enabled of [false, true]) {
      const merged = mergeVoicePreferences(
        { voiceReviewEveryRequest: enabled },
        current
      );
      expect(merged.voiceReviewEveryRequest).toBe(enabled);
      expect(merged.voiceEarcons).toBe(true);
      expect(merged.voiceSrAnnounceTranscripts).toBe(false);
      expect(merged.host).toBe('local');
    }
  });
  it('migrates legacy or invalid persisted state to off', () => {
    const current = { voiceReviewEveryRequest: true };
    for (const persisted of [null, {}, { voiceReviewEveryRequest: 'true' }]) {
      expect(
        mergeVoicePreferences(persisted, current).voiceReviewEveryRequest
      ).toBe(false);
    }
  });
});
