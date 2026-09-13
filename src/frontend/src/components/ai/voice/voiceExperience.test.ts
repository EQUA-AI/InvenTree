import { describe, expect, it } from 'vitest';
import { SilenceCheck, outputDisappeared } from './audioRoute';
import { cueAllowed } from './earcons';
import { TurnQueue } from './turnQueue';
import { initialVoiceSnapshot, legacyClientState } from './types';
import { ForegroundBounds } from './voiceLifecycle';

describe('foreground bounds', () => {
  it('expires exactly at five minutes, including a throttled hidden tab', () => {
    let now = 0;
    const bounds = new ForegroundBounds(300_000, () => now);
    now = 299_999;
    expect(bounds.expired).toBe(false);
    now++;
    expect(bounds.expired).toBe(true);
    bounds.touch();
    bounds.hide();
    now += 299_999;
    bounds.touch();
    expect(bounds.expired).toBe(false);
    now++;
    expect(bounds.show()).toBe(true);
  });
  it('repeated hidden notifications do not extend the deadline', () => {
    let now = 0;
    const bounds = new ForegroundBounds(300_000, () => now);
    bounds.hide();
    now = 250_000;
    bounds.hide();
    now = 300_000;
    expect(bounds.expired).toBe(true);
  });
});
it('backpressure rejects new speech without evicting accepted decisions', () => {
  const rejected: string[] = [];
  const queue = new TurnQueue<string>(2, (item) => rejected.push(item));
  expect(queue.push('confirm hold')).toBe(true);
  expect(queue.push('cancel the action')).toBe(true);
  expect(queue.push('new question')).toBe(false);
  expect(rejected).toEqual(['new question']);
  expect(queue.shift()).toBe('confirm hold');
  expect(queue.shift()).toBe('cancel the action');
});
it('microphone silence warns only for a full active window', () => {
  const check = new SilenceCheck(0.01, 1500);
  expect(check.sample(0, false, 0)).toBe(false);
  expect(check.sample(0, false, 8000)).toBe(false);
  expect(check.sample(0, true, 9000)).toBe(false);
  expect(check.sample(0, true, 10499)).toBe(false);
  expect(check.sample(0, true, 10500)).toBe(true);
  expect(check.sample(0.02, true, 11000)).toBe(false);
});
it('output loss is honest when enumeration has no supported outputs', () => {
  const device = (kind: MediaDeviceKind, deviceId: string) =>
    ({ kind, deviceId }) as MediaDeviceInfo;
  expect(outputDisappeared([], [])).toBe(false);
  expect(outputDisappeared([device('audioinput', 'mic')], [])).toBe(false);
  expect(outputDisappeared([device('audiooutput', 'default')], [])).toBe(false);
  expect(outputDisappeared([device('audiooutput', 'headset')], [])).toBe(true);
});
it.each(['muted', 'hidden', 'playing', 'sr'] as const)(
  'suppresses cues for %s',
  (field) => {
    const conditions = {
      enabled: true,
      muted: false,
      hidden: false,
      playing: false,
      sr: false
    };
    expect(cueAllowed(conditions)).toBe(true);
    expect(cueAllowed({ ...conditions, [field]: true })).toBe(false);
    expect(cueAllowed({ ...conditions, enabled: false })).toBe(false);
  }
);
it('legacy status is derived without confusing microphone and playback', () => {
  expect(legacyClientState(initialVoiceSnapshot)).toBe('unavailable');
  expect(
    legacyClientState({
      ...initialVoiceSnapshot,
      capability: { enabled: true } as never
    })
  ).toBe('ready');
});
