import type {
  MachineSignal,
  SeriesResponse,
  SignalTrend
} from '@lib/types/MachineHealth';

/** Adapt the bounded sampled reader for Health's single-signal chart. */
export function trendFromSeries(
  response: SeriesResponse,
  signal: MachineSignal
): SignalTrend {
  const entry = response.series.find(
    (item) => item.binding_id === signal.binding_id
  );
  return {
    binding_id: signal.binding_id,
    display_name: signal.display_name,
    unit: signal.unit,
    signal_kind: signal.signal_kind,
    source_id: signal.source_id,
    source_name: signal.source_name,
    source_type: signal.source_type,
    window_start: response.window_start,
    window_end: response.window_end,
    max_samples: response.slots ?? response.limits.max_points,
    limits: {
      max_window_seconds: response.limits.max_window_seconds,
      max_samples: response.limits.max_points
    },
    cadence_seconds: response.cadence_seconds,
    recorded_telemetry_as_live: response.recorded_telemetry_as_live,
    display_shifted: response.display_shifted,
    display_shift_seconds: response.display_shift_seconds,
    sampled: response.mode === 'sampled',
    resolution_seconds: response.resolution_seconds,
    available: entry?.available ?? false,
    reason: entry?.reason ?? (entry ? undefined : 'SOURCE_UNAVAILABLE'),
    detail: entry?.detail,
    samples: (entry?.samples ?? []).map((sample) => ({
      observed_at: new Date(sample.t).toISOString(),
      value:
        sample.raw === undefined
          ? sample.v
          : (sample.raw as string | number | boolean | null),
      quality: sample.q,
      plot_value: sample.v
    }))
  };
}
