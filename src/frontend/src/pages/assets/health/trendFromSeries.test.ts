import type { MachineSignal, SeriesResponse } from '@lib/types/MachineHealth';
import { describe, expect, it } from 'vitest';
import { trendFromSeries } from './trendFromSeries';

const signal = {
  binding_id: 9,
  display_name: 'Status',
  unit: '',
  signal_kind: 'state',
  source_id: 1,
  source_name: 'Station',
  source_type: 'scada'
} as MachineSignal;
const response = {
  window_start: '2026-09-21T00:00:00Z',
  window_end: '2026-10-21T00:00:00Z',
  slots: 240,
  mode: 'sampled',
  resolution_seconds: 10800,
  cadence_seconds: null,
  recorded_telemetry_as_live: true,
  display_shifted: true,
  display_shift_seconds: 1000,
  limits: { max_points: 4320, max_window_seconds: 2592000 },
  series: [
    {
      binding_id: 9,
      available: true,
      samples: [
        { t: 1000, v: 1, raw: 'R', q: 'good' },
        { t: 2000, v: null, q: 'bad' }
      ]
    }
  ]
} as SeriesResponse;

describe('Health long-window sampled source adapter', () => {
  it('keeps raw status, plotted value, missing quality and clock metadata', () => {
    const result = trendFromSeries(response, signal);
    expect(result.samples).toEqual([
      {
        observed_at: '1970-01-01T00:00:01.000Z',
        value: 'R',
        plot_value: 1,
        quality: 'good'
      },
      {
        observed_at: '1970-01-01T00:00:02.000Z',
        value: null,
        plot_value: null,
        quality: 'bad'
      }
    ]);
    expect(result).toMatchObject({
      sampled: true,
      resolution_seconds: 10800,
      recorded_telemetry_as_live: true,
      display_shifted: true,
      display_shift_seconds: 1000
    });
  });
  it('keeps failed and omitted targets unavailable without substituting current state', () => {
    expect(trendFromSeries({ ...response, series: [] }, signal)).toMatchObject({
      available: false,
      reason: 'SOURCE_UNAVAILABLE',
      samples: []
    });
    expect(
      trendFromSeries(
        {
          ...response,
          series: [
            {
              ...response.series[0],
              available: false,
              reason: 'SOURCE_TIMEOUT',
              samples: []
            }
          ]
        },
        signal
      )
    ).toMatchObject({
      available: false,
      reason: 'SOURCE_TIMEOUT',
      samples: []
    });
  });
});
