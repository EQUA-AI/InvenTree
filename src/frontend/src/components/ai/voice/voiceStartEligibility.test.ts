import { describe, expect, it } from 'vitest';

import { voiceStartBlockReason } from './voiceStartEligibility';

describe('voice start eligibility', () => {
  it('allows a start on an owned, idle composer', () => {
    expect(voiceStartBlockReason({})).toBeNull();
  });

  it('blocks starts that would bypass read-only or in-flight state', () => {
    expect(voiceStartBlockReason({ sharedThread: true })).toBe('read-only');
    expect(voiceStartBlockReason({ deletionPending: true })).toBe('deletion');
    expect(voiceStartBlockReason({ applyingScope: true })).toBe(
      'applying-scope'
    );
    expect(voiceStartBlockReason({ turnInFlight: true })).toBe(
      'turn-in-flight'
    );
    expect(voiceStartBlockReason({ syncing: true })).toBe('syncing');
  });

  it('reports the first blocking reason deterministically', () => {
    expect(
      voiceStartBlockReason({
        sharedThread: true,
        turnInFlight: true,
        syncing: true
      })
    ).toBe('read-only');
    expect(voiceStartBlockReason({ turnInFlight: true, syncing: true })).toBe(
      'turn-in-flight'
    );
  });
});
