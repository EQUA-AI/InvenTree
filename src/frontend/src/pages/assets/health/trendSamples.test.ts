import { describe, expect, it } from 'vitest';

import type { SignalTrend, TrendSample } from '@lib/types/MachineHealth';

import {
  isDrawn,
  isolatedIndices,
  numericValue,
  prepareTrend,
  sparklinePath,
  trendResolutionSeconds
} from './trendSamples';

const START = Date.parse('2026-10-18T08:50:00Z');
const at = (seconds: number) => new Date(START + seconds * 1000).toISOString();

function sample(
  seconds: number,
  value: TrendSample['value'],
  extra: Partial<TrendSample> = {}
): TrendSample {
  return { observed_at: at(seconds), value, quality: 'good', ...extra };
}

function trend(
  samples: TrendSample[],
  extra: Partial<SignalTrend> = {}
): SignalTrend {
  return {
    binding_id: 201,
    display_name: 'Discharge flow',
    unit: 'm3/h',
    signal_kind: 'flow',
    source_id: 1,
    source_name: 'Fixture historian',
    source_type: 'scada',
    window_start: at(0),
    window_end: at(600),
    max_samples: 4320,
    limits: {
      max_window_seconds: 21600,
      max_samples: 4320,
      expected_sample_interval_seconds: 5
    },
    cadence_seconds: 5,
    available: true,
    samples,
    ...extra
  };
}

/** The x coordinates of each subpath, in order. */
function segments(path: string): number[][] {
  return path
    .split('M')
    .filter(Boolean)
    .map((segment) =>
      segment
        .trim()
        .split(/\s*L/)
        .map((point) => Number(point.split(',')[0]))
    );
}

describe('Health trend reading', () => {
  it('keeps every reading at its own time and in time order', () => {
    const prepared = prepareTrend(
      trend([sample(10, 3), sample(0, 1), sample(5, null), sample(15, 4)])
    );
    expect(prepared.rows.map((row) => row.t - START)).toEqual([
      0, 5000, 10000, 15000
    ]);
    expect(prepared.rows.map((row) => row.value)).toEqual([1, null, 3, 4]);
    expect(prepared.measured).toBe(4);
    expect(prepared.drawn).toBe(3);
    expect(prepared.notNumeric).toBe(1);
  });

  it('keeps a recorded zero as a value, never a gap', () => {
    const prepared = prepareTrend(trend([sample(0, 0), sample(5, 0)]));
    expect(prepared.rows.every(isDrawn)).toBe(true);
    expect(prepared.drawn).toBe(2);
    expect(sparklinePath(prepared, 120, 28)).toBe('M0.0,28.0 L1.0,28.0');
  });

  it('draws a reading the source did not report good as a break', () => {
    const prepared = prepareTrend(
      trend([
        sample(0, 80),
        sample(5, 3276.7, { quality: 'bad' }),
        sample(10, 81)
      ])
    );
    expect(prepared.notGood).toBe(1);
    expect(prepared.rows[1]).toMatchObject({
      value: null,
      measured: true,
      undrawn: 'quality',
      raw: 3276.7
    });
  });

  it('marks a silence longer than the cadence with one display-only break', () => {
    const prepared = prepareTrend(
      trend([sample(0, 1), sample(5, 2), sample(10, 3), sample(130, 4)])
    );
    // 2.5 x the five-second cadence, at least fifteen seconds.
    expect(prepared.gapMs).toBe(15_000);
    expect(prepared.silences).toBe(1);
    const silence = prepared.rows[3];
    expect(silence).toEqual({
      t: START + 10_001,
      value: null,
      measured: false,
      from: START + 10_000,
      to: START + 130_000
    });
    // The break is never counted as a reading.
    expect(prepared.measured).toBe(4);
    expect(prepared.rows.filter((row) => row.measured)).toHaveLength(4);
  });

  it('does not break inside the normal cadence', () => {
    const prepared = prepareTrend(
      trend([sample(0, 1), sample(5, 2), sample(15, 3), sample(20, 4)])
    );
    expect(prepared.silences).toBe(0);
  });

  it('uses the expected interval when no cadence was measured, and nothing when neither is stated', () => {
    expect(
      trendResolutionSeconds({
        cadence_seconds: null,
        limits: {
          max_window_seconds: 1,
          max_samples: 1,
          expected_sample_interval_seconds: 5
        }
      })
    ).toBe(5);
    const unstated = prepareTrend(
      trend([sample(0, 1), sample(200, 2)], {
        cadence_seconds: undefined,
        limits: { max_window_seconds: 21600, max_samples: 4320 }
      })
    );
    expect(unstated.gapMs).toBeNull();
    expect(unstated.silences).toBe(0);
  });

  it('places the row graph by elapsed time across the window', () => {
    // Readings for the first fifteen seconds, then from the window's middle.
    const prepared = prepareTrend(
      trend([
        sample(0, 1),
        sample(5, 2),
        sample(10, 3),
        sample(300, 4),
        sample(305, 5),
        sample(600, 6)
      ])
    );
    const path = sparklinePath(prepared, 120, 28);
    expect(path).not.toBeNull();
    const xs = segments(path!);
    // 300 s into a 600 s window is the middle of 120 px, not the fourth of
    // six evenly spaced points (72 px).
    expect(xs).toEqual([
      [0, 1, 2],
      [60, 61],
      [120, 120]
    ]);
  });

  it('starts a new segment after a reading that cannot be drawn', () => {
    const prepared = prepareTrend(
      trend([sample(0, 1), sample(5, 2), sample(10, 'BAD'), sample(15, 3)])
    );
    const path = sparklinePath(prepared, 120, 28)!;
    expect(segments(path)).toHaveLength(2);
  });

  it('draws a reading alone between two breaks as a dot', () => {
    const prepared = prepareTrend(
      trend([sample(0, 1), sample(5, 2), sample(300, 3), sample(590, 4)])
    );
    const path = sparklinePath(prepared, 120, 28)!;
    expect(path).toContain('M60.0,');
    expect(path).toMatch(/M60\.0,(\d+\.\d) L60\.0,\1/);
    expect([...isolatedIndices(prepared.rows)]).toEqual([3, 5]);
  });

  it('has nothing to draw for an empty or single-reading window', () => {
    const empty = prepareTrend(trend([]));
    expect(empty.measured).toBe(0);
    expect(sparklinePath(empty, 120, 28)).toBeNull();
    expect(sparklinePath(prepareTrend(trend([sample(0, 1)])), 120, 28)).toBe(
      null
    );
  });

  it('widens the window to its readings and counts undated ones', () => {
    const prepared = prepareTrend(
      trend([
        sample(-30, 1),
        sample(0, 2),
        { observed_at: 'not a time', value: 3, quality: 'good' }
      ])
    );
    expect(prepared.start).toBe(START - 30_000);
    expect(prepared.undated).toBe(1);
    expect(prepared.measured).toBe(2);
  });

  it('reads status codes through plot_value and never coerces other text', () => {
    expect(numericValue({ value: 'R', plot_value: 1 })).toBe(1);
    expect(numericValue({ value: '12.5' })).toBe(12.5);
    expect(numericValue({ value: 'R', plot_value: null })).toBeNull();
    expect(numericValue({ value: true })).toBeNull();
    expect(numericValue({ value: null })).toBeNull();
  });

  it('prepares nothing from a missing trend', () => {
    const prepared = prepareTrend(null);
    expect(prepared.rows).toEqual([]);
    expect(prepared.end).toBeGreaterThan(prepared.start);
  });
});
