import { describe, expect, it } from 'vitest';
import type { LocatedMachine } from './locationTypes';
import { pageFacts } from './pageFacts';
const row = (state: string, stale = 0, alarms?: unknown) =>
  ({
    condition: {
      state,
      configured: true,
      signal_count: 6,
      stale_signal_count: stale,
      degraded_data: stale > 0
    },
    open_alarms: alarms
  }) as LocatedMachine;
describe('loaded machine page facts', () => {
  it('keeps health, offline, stale and unknown separate from application alarms', () => {
    expect(
      pageFacts([
        row('normal'),
        row('warning', 2, 3),
        row('critical', 0, 1),
        row('offline', 6),
        row('unknown'),
        { condition: null } as LocatedMachine
      ])
    ).toEqual({
      critical: 1,
      warning: 1,
      normal: 1,
      offline: 1,
      unknown: 1,
      unavailable: 1,
      stale: 2,
      alarmRows: null
    });
  });
  it('does not treat a partial or invalid alarm count as zero', () => {
    for (const count of [null, undefined, -1, Number.NaN, 1.5, '0'])
      expect(pageFacts([row('normal', 0, count)]).alarmRows).toBeNull();
    expect(pageFacts([row('unknown', 0, 0)]).alarmRows).toBe(0);
  });
  it('does not turn active records or missing condition into running or normal', () => {
    expect(pageFacts([{ active: true } as LocatedMachine]).unavailable).toBe(1);
    expect(pageFacts([row('normal', 7)])).toMatchObject({
      unavailable: 1,
      critical: 0,
      warning: 0
    });
    expect(pageFacts([])).toEqual({
      critical: 0,
      warning: 0,
      normal: 0,
      unavailable: 0,
      offline: 0,
      unknown: 0,
      stale: 0,
      alarmRows: 0
    });
  });
});

it('counts alarmed rows rather than summing overlapping station and pump alarm totals', () => {
  expect(
    pageFacts([row('warning', 2, 4), row('warning', 2, 2), row('normal', 0, 0)])
      .alarmRows
  ).toBe(2);
});

it('exclusive counts sum to the loaded rows, with stale explicitly overlapping', () => {
  const rows = ['normal', 'critical', 'warning', 'offline', 'unknown'].map(
    (state) => row(state, state === 'offline' ? 6 : 0, 0)
  );
  rows.push({ condition: null } as LocatedMachine);
  const f = pageFacts(rows);
  expect(
    f.normal + f.critical + f.warning + f.offline + f.unknown + f.unavailable
  ).toBe(rows.length);
  expect(f.stale).toBe(1);
});
