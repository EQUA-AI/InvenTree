import { describe, expect, it } from 'vitest';

import { machineCondition } from './machineCondition';

const condition = (changes: Record<string, unknown> = {}) => ({
  state: 'normal',
  configured: true,
  signal_count: 4,
  stale_signal_count: 0,
  degraded_data: false,
  ...changes
});

describe('Machines list condition', () => {
  it('keeps the Health state and says nothing more when coverage is full', () => {
    expect(machineCondition(condition({ state: 'warning' }))).toEqual({
      kind: 'assessed',
      state: 'warning',
      coverage: { kind: 'full' }
    });
  });

  it.each([
    [
      { configured: false, signal_count: 0, state: 'unknown' },
      { kind: 'unmapped' }
    ],
    [
      { state: 'offline', stale_signal_count: 4, degraded_data: true },
      { kind: 'no-current-data' }
    ],
    [
      { stale_signal_count: 1, degraded_data: true },
      { kind: 'stale', stale: 1, signals: 4 }
    ],
    [{ degraded_data: true }, { kind: 'poor-quality' }]
  ])('names one coverage limit for %j', (changes, coverage) => {
    const summary = machineCondition(condition(changes));
    expect(summary.kind === 'assessed' && summary.coverage).toEqual(coverage);
  });

  it('keeps an anomaly-driven state on a machine with nothing mapped', () => {
    expect(
      machineCondition(
        condition({ configured: false, signal_count: 0, state: 'critical' })
      )
    ).toEqual({
      kind: 'assessed',
      state: 'critical',
      coverage: { kind: 'unmapped' }
    });
  });

  it.each([
    null,
    undefined,
    'normal',
    [],
    {},
    condition({ state: 'healthy' }),
    condition({ configured: 'yes' }),
    condition({ signal_count: -1 }),
    condition({ stale_signal_count: 5 }),
    condition({ degraded_data: null })
  ])('is not available rather than normal for %j', (value) => {
    expect(machineCondition(value)).toEqual({ kind: 'unavailable' });
  });
});
