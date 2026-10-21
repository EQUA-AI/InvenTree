import { afterEach, describe, expect, it, vi } from 'vitest';
import { displayClockLabel, displayInstant } from './displayClock';
const initial = process.env.TZ;
afterEach(() => {
  process.env.TZ = initial;
  vi.restoreAllMocks();
});
describe('the declared browser clock', () => {
  it.each([
    ['Asia/Kolkata', '18 Oct 2026 14:30:00', 'UTC+05:30'],
    ['Pacific/Honolulu', '17 Oct 2026 23:00:00', 'UTC-10:00'],
    ['Europe/Berlin', '18 Oct 2026 11:00:00', 'UTC+02:00']
  ])(
    'formats %s without assuming whole-hour offsets or the UTC date',
    (zone, expected, offset) => {
      process.env.TZ = zone;
      const at = Date.parse('2026-10-18T09:00:00Z');
      expect(displayInstant(at, { offsetIfDiffers: true, pageAt: at })).toBe(
        expected
      );
      expect(displayClockLabel(at)).toContain(offset);
    }
  );
  it('marks a historical offset that differs across DST', () => {
    process.env.TZ = 'Europe/Berlin';
    const pageAt = Date.parse('2026-07-01T12:00:00Z');
    expect(
      displayInstant('2026-01-01T12:00:00Z', { offsetIfDiffers: true, pageAt })
    ).toBe('1 Jan 2026 13:00:00 UTC+01:00');
    expect(
      displayInstant('2026-07-01T12:00:00Z', { offsetIfDiffers: true, pageAt })
    ).toBe('1 Jul 2026 14:00:00');
  });
  it('does not invent a zone', () => {
    vi.spyOn(Intl, 'DateTimeFormat').mockReturnValue({
      resolvedOptions: () => ({ timeZone: undefined })
    } as unknown as Intl.DateTimeFormat);
    expect(displayClockLabel()).toBe('zone unavailable');
  });
  it('rejects absent, invalid and uninitialized numeric times', () => {
    for (const value of [
      null,
      undefined,
      '',
      'bad timestamp',
      Number.NaN,
      Number.POSITIVE_INFINITY,
      -1,
      0
    ])
      expect(displayInstant(value)).toBeNull();
  });
});
