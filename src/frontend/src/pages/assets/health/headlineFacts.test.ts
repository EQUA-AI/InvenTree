import type {
  MachineAnomaly,
  MachineHealthSummary,
  MachineSignal
} from '@lib/types/MachineHealth';
import { expect, it } from 'vitest';
import { headlineFacts } from './headlineFacts';
import { shiftFacts } from './shiftFacts';
const summary = (state = 'normal') =>
  ({
    state,
    anomaly_counts: { critical: 0, warning: 0, info: 0 }
  }) as MachineHealthSummary;
const signal = (changes: Partial<MachineSignal> = {}) =>
  ({
    binding_id: 1,
    display_name: 'Synthetic signal',
    active: true,
    value: 1,
    observed_at: '2026-10-18T08:59:40Z',
    stale: false,
    quality: 'good',
    state: 'normal',
    critical_designation: { critical: true },
    ...changes
  }) as MachineSignal;
it('keeps partial and unavailable assessments neutral even when the server says Normal', () => {
  expect(headlineFacts(summary(), null).coverageIncomplete).toBe(true);
  expect(
    headlineFacts(summary(), [signal({ critical_designation: null })])
      .coverageIncomplete
  ).toBe(true);
  expect(
    headlineFacts(summary(), [
      signal(),
      signal({ binding_id: 2, state: 'unknown' })
    ]).coverageIncomplete
  ).toBe(true);
  expect(headlineFacts(summary(), [signal()]).coverageIncomplete).toBe(false);
});
it('lists each designated coverage gap and its actual reason', () => {
  const rows = [
    signal({
      binding_id: 1,
      critical_designation: {
        critical: true,
        review_required: true
      } as MachineSignal['critical_designation']
    }),
    signal({ binding_id: 2, stale: true, state: 'unknown' }),
    signal({ binding_id: 3, value: null }),
    signal({ binding_id: 4, quality: 'bad' }),
    signal({ binding_id: 5, state: 'unknown' })
  ];
  expect(
    headlineFacts(summary(), rows).criticalMissing.map((row) => row.reason)
  ).toEqual([
    'review required',
    'not current',
    'no observation',
    'quality not good',
    'not evaluated'
  ]);
});
it('qualifies no observation separately from unknown classification', () => {
  expect(
    headlineFacts(summary('unknown'), [
      signal({ value: null, state: 'unknown' })
    ]).noObservation
  ).toBe(true);
  expect(
    headlineFacts(summary('unknown'), [signal({ state: 'unknown' })])
      .noObservation
  ).toBe(false);
});
it('explains a server breach from matching signals, then supplied alarms, otherwise unavailable', () => {
  expect(
    headlineFacts(summary('critical'), [signal({ state: 'critical' })]).why
  ).toBe('signals');
  expect(
    headlineFacts(
      summary('critical'),
      [],
      [{ title: 'Synthetic anomaly' } as MachineAnomaly]
    ).why
  ).toBe('anomaly');
  expect(headlineFacts(summary('critical'), []).why).toBe('unavailable');
  expect(headlineFacts(summary(), []).why).toBe('not-needed');
});
it.each([
  [10800, 'later', '+', '2026-10-18T06:00:00Z'],
  [-10800, 'earlier', '−', '2026-10-18T12:00:00Z']
] as const)(
  'keeps signed shift %s and recorded time distinct',
  (offset, direction, sign, recorded) => {
    const result = shiftFacts(offset, '2026-10-18T09:00:00Z');
    expect(result).toEqual({
      direction,
      seconds: 10800,
      sign,
      recorded: Date.parse(recorded)
    });
  }
);
it('does not fabricate a shift amount or recorded instant', () => {
  for (const value of [null, undefined, Number.NaN, 0])
    expect(shiftFacts(value).direction).toBe('unknown');
  expect(shiftFacts(3, 'bad').recorded).toBeNull();
});
