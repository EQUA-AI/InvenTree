import { describe, expect, it } from 'vitest';
import { displayClockLabel, displayInstant } from './displayClock';
describe('monitoring display clock', () => {
  it('uses the actual browser zone and offset', () => {
    expect(displayClockLabel()).toContain(
      Intl.DateTimeFormat().resolvedOptions().timeZone
    );
    expect(displayClockLabel()).toMatch(/UTC[+-]\d{2}:\d{2}/);
  });
  it('formats full dates on a 24 hour clock', () => {
    expect(displayInstant('2026-10-18T09:00:00Z')).toMatch(
      /18 Oct 2026 \d{2}:00:00/
    );
    expect(displayInstant(0)).not.toBeNull();
  });
  it('leaves invalid and absent instants unavailable', () => {
    for (const value of [null, undefined, '', 'bad timestamp', Number.NaN])
      expect(displayInstant(value)).toBeNull();
  });
});
