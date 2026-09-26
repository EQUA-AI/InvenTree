import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { MachineSignal, SeriesResponse } from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';

/**
 * Data access for the Performance tab.
 *
 * Two reads carry the page. `signals/` is the machine's current state - cheap,
 * database-only, and what every KPI tile shows. `series/` is the federated
 * history read - expensive, so it is made exactly once per window for every
 * parameter on the page, never once per chart.
 *
 * "Live" here means re-asking on the cadence at which new data can actually
 * appear. The plant writes every five seconds, but this deployment polls the
 * source once a minute, so a series is re-read once a minute and the current
 * values a little more often. Anything faster would be theatre.
 */

export type RangePreset = '15m' | '1h' | '6h' | '12h' | '24h';

export const PRESET_SECONDS: Record<RangePreset, number> = {
  '15m': 15 * 60,
  '1h': 3600,
  '6h': 6 * 3600,
  '12h': 12 * 3600,
  '24h': 24 * 3600
};

/** Either "the last N seconds", re-anchored at every fetch, or a fixed span. */
export type WindowSpec =
  | { kind: 'relative'; seconds: number }
  | { kind: 'absolute'; from: number; to: number };

export function windowSeconds(spec: WindowSpec): number {
  return spec.kind === 'relative'
    ? spec.seconds
    : Math.max(1, Math.round((spec.to - spec.from) / 1000));
}

/** Resolve a spec to concrete edges, in display-clock epoch milliseconds. */
export function resolveWindow(
  spec: WindowSpec,
  now: number
): { from: number; to: number } {
  if (spec.kind === 'absolute') {
    return { from: spec.from, to: spec.to };
  }
  return { from: now - spec.seconds * 1000, to: now };
}

/**
 * Points to ask for, by window length.
 *
 * Every point is a whole-station snapshot of about 50 KB, so the cost of a
 * sampled read is its point count, not its window. An hour at 120 points is a
 * reading every 30 s, which a chart a few hundred pixels wide cannot tell from
 * one every 15 s, and half the transfer. Longer windows earn more points
 * because their resolution is coarser to begin with.
 */
export function pointsFor(seconds: number): number {
  if (seconds <= 3600) return 120;
  if (seconds <= 6 * 3600) return 180;
  return 240;
}

/** How often to re-read while live, matched to the source poll. */
const LIVE_SERIES_INTERVAL_MS = 60 * 1000;
const LIVE_SIGNALS_INTERVAL_MS = 15 * 1000;

export function useMachineSignals(machineId: number, live: boolean) {
  const api = useApi();
  return useQuery<MachineSignal[]>({
    // Shared with the Health tab so the two never disagree about a value.
    queryKey: ['machine-health-signals', machineId],
    refetchInterval: live ? LIVE_SIGNALS_INTERVAL_MS : false,
    refetchIntervalInBackground: false,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_signals, machineId)
      );
      return response.data?.results ?? [];
    }
  });
}

export interface SeriesTargets {
  bindings?: readonly number[];
  keys?: readonly string[];
}

export function useMachineSeries(
  machineId: number,
  targets: SeriesTargets,
  window: WindowSpec,
  live: boolean,
  points: number = pointsFor(windowSeconds(window))
) {
  const api = useApi();
  const bindings = useMemo(
    () => [...new Set(targets.bindings ?? [])].sort((a, b) => a - b),
    [targets.bindings]
  );
  const keys = useMemo(
    () => [...new Set(targets.keys ?? [])].sort(),
    [targets.keys]
  );
  const windowKey =
    window.kind === 'relative'
      ? `last:${window.seconds}`
      : `${window.from}:${window.to}`;
  const enabled = bindings.length > 0 || keys.length > 0;

  return useQuery<SeriesResponse>({
    queryKey: [
      'machine-health-series',
      machineId,
      bindings.join(','),
      keys.join(','),
      windowKey,
      points
    ],
    enabled,
    // Keep the last window on screen while the next one loads: a chart that
    // blanks on every refetch reads as a fault, not as a refresh.
    placeholderData: keepPreviousData,
    staleTime: 30 * 1000,
    refetchInterval:
      live && window.kind === 'relative' ? LIVE_SERIES_INTERVAL_MS : false,
    refetchIntervalInBackground: false,
    retry: false,
    queryFn: async () => {
      // "Now" is taken at fetch time, so a live window keeps moving.
      const { from, to } = resolveWindow(window, Date.now());
      const params: Record<string, string> = {
        from: new Date(from).toISOString(),
        to: new Date(to).toISOString(),
        points: String(points)
      };
      if (bindings.length > 0) params.bindings = bindings.join(',');
      if (keys.length > 0) params.keys = keys.join(',');
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_series, machineId),
        {
          params,
          // A sampled day is a few hundred small reads run concurrently; a
          // complete twenty minutes is ~240 documents. Neither fits the
          // global 5 s default.
          timeout: 90 * 1000
        }
      );
      return response.data;
    }
  });
}

/** What history the source holds for this machine's station. */
export interface DataRange {
  available: boolean;
  from?: string;
  to?: string;
  reason?: string;
  source_name?: string;
}

export function useDataRange(machineId: number) {
  const api = useApi();
  return useQuery<DataRange>({
    queryKey: ['machine-health-data-range', machineId],
    staleTime: 60 * 60 * 1000,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_data_range, machineId)
      );
      return response.data;
    }
  });
}
