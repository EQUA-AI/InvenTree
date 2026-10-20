import { describe, expect, it } from 'vitest';
import {
  DEFAULT_VOICE_PROGRESS_MODE,
  mergeVoicePreferences,
  normalizeVoiceProgressMode
} from './types';

describe('voice progress preference (Off / Brief / Guided)', () => {
  it('defaults to Brief and safely normalizes anything invalid', () => {
    expect(DEFAULT_VOICE_PROGRESS_MODE).toBe('brief');
    expect(normalizeVoiceProgressMode('off')).toBe('off');
    expect(normalizeVoiceProgressMode('brief')).toBe('brief');
    expect(normalizeVoiceProgressMode('guided')).toBe('guided');
    for (const bad of [
      undefined,
      null,
      '',
      'loud',
      'OFF',
      'Brief',
      7,
      {},
      []
    ]) {
      expect(normalizeVoiceProgressMode(bad)).toBe('brief');
    }
  });
  it('persists each mode through the local preference merge', () => {
    const current = {
      voiceEarcons: true,
      voiceSrAnnounceTranscripts: false,
      voiceProgressMode: DEFAULT_VOICE_PROGRESS_MODE
    };
    for (const mode of ['off', 'brief', 'guided'] as const) {
      const merged = mergeVoicePreferences(
        { voiceProgressMode: mode },
        current
      );
      expect(merged.voiceProgressMode).toBe(mode);
      // Off suppresses optional spoken waiting ONLY: the earcon and
      // screen-reader transcript preferences are never touched by it.
      expect(merged.voiceEarcons).toBe(true);
      expect(merged.voiceSrAnnounceTranscripts).toBe(false);
    }
  });
  it('renormalizes an invalid persisted mode without dropping other state', () => {
    const current: Record<string, unknown> = { voiceEarcons: true };
    const merged = mergeVoicePreferences(
      { voiceProgressMode: 'shout', host: 'h1' },
      current
    );
    expect(merged.voiceProgressMode).toBe('brief');
    expect(merged.host).toBe('h1');
    expect(merged.voiceEarcons).toBe(true);
    // Legacy storage with no mode gets the Brief default.
    expect(mergeVoicePreferences({}, current).voiceProgressMode).toBe('brief');
    expect(
      mergeVoicePreferences({ voiceProgressMode: 'guided' }, current)
        .voiceProgressMode
    ).toBe('guided');
  });
});
