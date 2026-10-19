import { describe, expect, it } from 'vitest';

import type { SignalTrend } from '@lib/types/MachineHealth';

import { indexSuppliedTrends, suppliedTrend } from './suppliedTrends';

function trend(bindingId: number): SignalTrend {
  return {
    binding_id: bindingId,
    display_name: 'Fixture signal',
    unit: '',
    signal_kind: 'status',
    source_id: 10,
    source_name: 'Synthetic historian',
    source_type: 'historian',
    window_start: '2026-10-18T00:00:00Z',
    window_end: '2026-10-18T00:10:00Z',
    max_samples: 120,
    limits: { max_window_seconds: 21600, max_samples: 120 },
    available: true,
    samples: [
      { observed_at: '2026-10-18T00:00:00Z', value: 0, quality: 'good' },
      { observed_at: '2026-10-18T00:00:05Z', value: null, quality: 'unknown' },
      {
        observed_at: '2026-10-18T00:07:00Z',
        value: false,
        quality: 'uncertain'
      },
      {
        observed_at: '2026-10-18T00:09:37Z',
        value: 'unmapped',
        quality: 'bad',
        plot_value: null
      }
    ]
  };
}

describe('supplied Health trend adapter', () => {
  it.each([undefined, []])(
    'keeps a pending/empty supplier managed: %s',
    (batch) => {
      const result = suppliedTrend(indexSuppliedTrends(batch), 1);
      expect(result).toBeNull();
      // SignalTrendSparkline enables its individual query only for undefined.
      expect(result).not.toBeUndefined();
    }
  );

  it('returns the original object and null for an omitted binding', () => {
    const original = trend(1);
    const index = indexSuppliedTrends([original]);
    expect(suppliedTrend(index, 1)).toBe(original);
    expect(suppliedTrend(index, 2)).toBeNull();
  });

  it('never matches repeated display names instead of binding IDs', () => {
    const first = trend(1);
    const second = trend(2);
    const index = indexSuppliedTrends([first, second]);
    expect(suppliedTrend(index, 1)).toBe(first);
    expect(suppliedTrend(index, 2)).toBe(second);
  });

  it('preserves unavailable source metadata', () => {
    const unavailable = {
      ...trend(1),
      available: false,
      reason: 'fixture_unavailable',
      detail: 'No fixture history',
      truncated: true,
      samples: []
    };
    expect(suppliedTrend(indexSuppliedTrends([unavailable]), 1)).toBe(
      unavailable
    );
  });

  it('preserves raw samples, gaps, clocks and provenance without mutation', () => {
    const original = trend(1);
    const before = structuredClone(original);
    for (const sample of original.samples) Object.freeze(sample);
    Object.freeze(original.samples);
    Object.freeze(original);
    const batch = Object.freeze([original]);
    const result = suppliedTrend(indexSuppliedTrends(batch), 1);
    expect(result).toEqual(before);
    expect(result?.samples).toBe(original.samples);
    expect(batch).toEqual([before]);
  });

  it('retains the existing last-entry behavior for duplicate binding IDs', () => {
    const first = trend(1);
    const last = { ...trend(1), available: false };
    const index = indexSuppliedTrends([first, last]);
    expect(index.size).toBe(1);
    expect(suppliedTrend(index, 1)).toBe(last);
  });

  it('does not retain entries from a previous machine batch', () => {
    const previous = indexSuppliedTrends([trend(1)]);
    const current = indexSuppliedTrends([trend(2)]);
    expect(suppliedTrend(current, 1)).toBeNull();
    expect(suppliedTrend(previous, 2)).toBeNull();
  });
});
