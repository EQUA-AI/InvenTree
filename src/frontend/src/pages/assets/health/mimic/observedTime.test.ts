import { describe, expect, it } from 'vitest';
import { observedTime } from './observedTime';

describe('alarm observation display zone', () => {
  const instant = '2026-10-20T08:00:00Z';
  it('labels the browser display zone when no plant setting is supplied', () => {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(observedTime(instant)).toBe(
      `${new Date(instant).toLocaleString(undefined, { timeZone: zone })} (${zone})`
    );
  });
  it('uses the supplied plant zone, independently of the browser zone', () => {
    expect(observedTime(instant, 'America/Chicago')).toBe(
      `${new Date(instant).toLocaleString(undefined, { timeZone: 'America/Chicago' })} (America/Chicago)`
    );
  });
  it('labels the fallback zone when configuration is invalid', () => {
    expect(observedTime(instant, 'invalid')).toBe(observedTime(instant));
  });
  it('does not turn an invalid timestamp into a reading', () => {
    expect(observedTime('invalid')).toBeNull();
  });
});
