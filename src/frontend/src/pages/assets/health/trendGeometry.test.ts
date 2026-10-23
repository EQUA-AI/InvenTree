import type { SignalTrend, TrendSample } from '@lib/types/MachineHealth';
import { describe, expect, it } from 'vitest';
import { trendGeometry } from './trendGeometry';
import { prepareTrend } from './trendSamples';

const sample = (
  minute: number,
  value: TrendSample['value'],
  quality: TrendSample['quality'] = 'good'
): TrendSample => ({
  observed_at: new Date(Date.UTC(2026, 9, 21, 0, minute)).toISOString(),
  value,
  quality
});

describe('timestamp-based trend geometry', () => {
  it('sorts readings and preserves unequal time intervals', () => {
    const result = trendGeometry([
      sample(10, 20),
      sample(0, 10),
      sample(1, 15)
    ])!;
    expect(result.points.map((point) => point.x)).toEqual([0, 12, 120]);
  });
  it('breaks the line at nulls and excludes booleans and empty strings', () => {
    const result = trendGeometry([
      sample(0, 10),
      sample(1, null),
      sample(2, true),
      sample(3, ''),
      sample(4, 20)
    ])!;
    expect(result.path.match(/M/g)).toHaveLength(2);
    expect(result.path).not.toContain('L');
    expect(result.min).toBe(10);
    expect(result.excluded).toBe(3);
  });
  it('breaks at uncertain and bad readings rather than treating them as evidence', () => {
    const result = trendGeometry([
      sample(0, 10),
      sample(1, 1000, 'uncertain'),
      sample(2, -500, 'bad'),
      sample(3, 20)
    ])!;
    expect(result.min).toBe(10);
    expect(result.max).toBe(20);
    expect(result.path.match(/M/g)).toHaveLength(2);
    expect(result.uncertain).toBe(1);
    expect(result.bad).toBe(1);
  });
  it('centers a flat signal and does not invent data for unusable readings', () => {
    expect(
      trendGeometry([sample(0, 12), sample(1, 12)])!.points.map(
        (point) => point.y
      )
    ).toEqual([14, 14]);
    expect(trendGeometry([sample(0, null), sample(1, 'NaN')])).toBeNull();
    expect(trendGeometry([])).toBeNull();
  });
  it('preserves full read windows, cadence gaps and server-mapped state values', () => {
    const samples = [
      { ...sample(0, 'RUN'), plot_value: 1 },
      { ...sample(1, 'STOP'), plot_value: 0 },
      { ...sample(9, 'RUN'), plot_value: 1 }
    ];
    const prepared = prepareTrend({
      samples,
      window_start: sample(0, 0).observed_at,
      window_end: sample(10, 0).observed_at,
      cadence_seconds: 60
    } as SignalTrend);
    const result = trendGeometry(samples, 120, 28, prepared)!;
    expect(result.points.map((point) => point.x)).toEqual([0, 12, 108]);
    expect(result.path.match(/M/g)).toHaveLength(2);
    expect(result.excluded).toBe(0);
    expect(result.min).toBe(0);
    expect(result.max).toBe(1);
  });
});
