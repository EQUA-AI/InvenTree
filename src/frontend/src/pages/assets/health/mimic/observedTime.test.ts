import { describe, expect, it } from 'vitest';
import { displayClock, formatInstant } from './observedTime';
describe('operational display clock', () => {
  const instant = '2026-10-20T08:00:00Z';
  it('labels the browser clock, with the missing plant declaration', () => {
    const clock = displayClock();
    expect(clock.source).toBe('browser');
    expect(clock.zone).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
    expect(clock.label).toContain('your clock');
    expect(clock.label).toContain('plant timezone not supplied');
  });
  it('uses the supplied, validated IANA plant clock', () => {
    const clock = displayClock('Asia/Kolkata');
    expect(clock.source).toBe('plant');
    expect(clock.label).toContain('plant clock');
    expect(formatInstant(instant, clock)).toBe('20 Oct 2026 13:30:00');
  });
  it('labels invalid configuration as the browser fallback', () => {
    const fallback = displayClock('invalid');
    expect(fallback.zone).toBe(displayClock().zone);
    expect(fallback.source).toBe('browser');
    expect(fallback.label).toContain('plant timezone invalid');
  });
  it('never formats an invalid or absent observation', () => {
    for (const value of ['invalid', '', null, undefined])
      expect(formatInstant(value, displayClock())).toBeNull();
  });
  it('uses a 24-hour clock with seconds', () => {
    expect(
      formatInstant(instant, displayClock('UTC'), { timeOnly: true })
    ).toBe('08:00:00');
  });
});
