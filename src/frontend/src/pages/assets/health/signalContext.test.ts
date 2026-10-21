import type { MachineAnomaly } from '@lib/types/MachineHealth';
import { describe, expect, it } from 'vitest';
import { anomaliesForSignal, recordedAnomalyBounds } from './signalContext';

const alarm = (pk: number, bindings: number[]): MachineAnomaly =>
  ({
    pk,
    status: 'open',
    signals: bindings.map((binding_id) => ({ binding_id }))
  }) as MachineAnomaly;

describe('stable occurrence bounds', () => {
  const occurrence = {
    ...alarm(901, [201]),
    first_observed_at: '2026-10-17T06:00:00Z',
    last_observed_at: '2026-10-17T06:10:00Z'
  };
  it('recovers the recorded window with the anomaly offset, independently of the current clock', () => {
    expect(
      recordedAnomalyBounds({ ...occurrence, display_shift_seconds: 31536000 })
    ).toEqual({
      from: '2025-10-17T06:00:00.000Z',
      to: '2025-10-17T06:10:00.000Z'
    });
    expect(
      recordedAnomalyBounds({
        ...occurrence,
        source: null,
        display_shift_seconds: 0
      })
    ).toEqual({
      from: '2026-10-17T06:00:00.000Z',
      to: '2026-10-17T06:10:00.000Z'
    });
  });
  it('fails closed for absent or nonfinite offsets, including a null source', () => {
    for (const display_shift_seconds of [
      undefined,
      null,
      Number.NaN,
      Number.POSITIVE_INFINITY
    ])
      expect(
        recordedAnomalyBounds({
          ...occurrence,
          source: null,
          display_shift_seconds
        })
      ).toBeNull();
  });
  it('does not invent a duration or throw for an invalid or overflowing date', () => {
    expect(
      recordedAnomalyBounds({
        ...occurrence,
        display_shift_seconds: 0,
        last_observed_at: occurrence.first_observed_at
      })
    ).toEqual({
      from: '2026-10-17T06:00:00.000Z',
      to: '2026-10-17T06:00:00.000Z'
    });
    expect(
      recordedAnomalyBounds({
        ...occurrence,
        display_shift_seconds: Number.MAX_VALUE,
        first_observed_at: 'invalid'
      })
    ).toEqual({ from: null, to: null });
  });
});

describe('canonical signal association', () => {
  it('cites binding ids rather than matching display names', () => {
    const rows = [alarm(1, [201]), alarm(2, [202])];
    expect(anomaliesForSignal(rows, 201)).toEqual({
      cited: [rows[0]],
      unassociated: []
    });
  });
  it('keeps missing association separate from a known absence', () => {
    const rows = [alarm(1, [])];
    expect(anomaliesForSignal(rows, 201)).toEqual({
      cited: [],
      unassociated: rows
    });
  });
  it('does not duplicate an anomaly with multiple citations to a binding', () => {
    expect(anomaliesForSignal([alarm(1, [201, 201])], 201).cited).toHaveLength(
      1
    );
  });
  it('resolved and suppressed records do not become active row anomalies', () => {
    const rows = [
      alarm(1, [201]),
      { ...alarm(2, [201]), status: 'resolved' as const },
      { ...alarm(3, []), status: 'suppressed' as const }
    ];
    expect(anomaliesForSignal(rows, 201)).toEqual({
      cited: [rows[0]],
      unassociated: []
    });
  });
  it('a known different binding is not an unassociated anomaly', () => {
    expect(anomaliesForSignal([alarm(1, [202])], 201)).toEqual({
      cited: [],
      unassociated: []
    });
  });
});
