import type { MachineHealthSummary } from '@lib/types/MachineHealth';
import type { AxiosInstance } from 'axios';
import type { MaintenanceResult } from '../../../components/dashboard/widgets/maintenanceMetricsRead';
import { InvalidReadResponse } from '../../../functions/readQueryPolicy';
import {
  type LocatedMachine,
  type PageResult,
  locationApi
} from './locationTypes';

export interface MachineScope {
  location: number | null;
  direct: boolean;
}

export function parseMachineScope(params: URLSearchParams): MachineScope {
  const raw = params.get('location');
  const location = raw && /^\d+$/.test(raw) ? Number(raw) : null;
  return {
    location:
      location != null && Number.isSafeInteger(location) && location > 0
        ? location
        : null,
    direct: params.get('scope') === 'direct'
  };
}

export function machineScopeHref(base: string, scope: MachineScope): string {
  const params = new URLSearchParams();
  if (scope.location != null) params.set('location', String(scope.location));
  if (scope.direct) params.set('scope', 'direct');
  return params.size ? `${base}?${params}` : base;
}

/** Read all placement pages; inventory search and inventory paging are unrelated. */
export async function readLocationMachines(
  api: Pick<AxiosInstance, 'get'>,
  location: number | undefined,
  descendants: boolean,
  signal: AbortSignal,
  options?: { activeOnly?: boolean }
): Promise<LocatedMachine[]> {
  const machines = new Map<number, LocatedMachine>();
  let offset = 0;
  let total: number | undefined;
  while (true) {
    signal.throwIfAborted();
    const { data } = await api.get<PageResult<LocatedMachine>>(
      `${locationApi}machines/`,
      {
        signal,
        params: {
          location,
          include_descendants: descendants,
          ...(options?.activeOnly ? { active: true } : {}),
          limit: 100,
          offset
        }
      }
    );
    if (
      !Number.isSafeInteger(data?.count) ||
      data.count < 0 ||
      !Array.isArray(data.results) ||
      (total !== undefined && total !== data.count) ||
      data.results.some(
        (m) => !Number.isSafeInteger(m.pk) || m.pk <= 0 || machines.has(m.pk)
      )
    ) {
      throw new InvalidReadResponse('Incomplete placement population');
    }
    total = data.count;
    for (const machine of data.results) machines.set(machine.pk, machine);
    offset += data.results.length;
    if (!data.next) {
      if (machines.size !== total)
        throw new InvalidReadResponse('Incomplete placement pagination');
      return [...machines.values()];
    }
    if (!data.results.length || offset >= total)
      throw new InvalidReadResponse('Invalid placement pagination');
  }
}

/** Bounded fan-out; a failed member never becomes a successful partial sum. */
export async function mapMachines<T, R>(
  items: readonly T[],
  signal: AbortSignal,
  read: (item: T) => Promise<R>,
  concurrency = 4
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0;
  let failed = false;
  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, async () => {
      while (!failed) {
        signal.throwIfAborted();
        const index = next++;
        if (index >= items.length) return;
        try {
          results[index] = await read(items[index]);
        } catch (error) {
          failed = true;
          throw error;
        }
      }
    })
  );
  return results;
}

export function parseHealthSummary(
  data: MachineHealthSummary
): MachineHealthSummary {
  if (
    !data ||
    !['normal', 'warning', 'critical', 'offline', 'unknown'].includes(
      data.state
    ) ||
    typeof data.configured !== 'boolean' ||
    typeof data.degraded_data !== 'boolean' ||
    !Number.isSafeInteger(data.signal_count) ||
    data.signal_count < 0 ||
    !Number.isSafeInteger(data.stale_signal_count) ||
    data.stale_signal_count < 0 ||
    data.stale_signal_count > data.signal_count ||
    (data.last_observed_at !== null &&
      !Number.isFinite(Date.parse(data.last_observed_at)))
  ) {
    throw new InvalidReadResponse('Invalid machine health summary');
  }
  return data;
}

export function healthCounts(rows: readonly MachineHealthSummary[]) {
  return {
    critical: rows.filter((r) => r.state === 'critical').length,
    unknown: rows.filter((r) => r.state === 'unknown' || r.state === 'offline')
      .length,
    unconfigured: rows.filter((r) => !r.configured).length,
    degraded: rows.filter((r) => r.degraded_data).length,
    stale: rows.filter((r) => r.stale_signal_count > 0).length
  };
}

export interface MachineWorkMetrics {
  machine: number;
  open: MaintenanceResult;
  overdue: MaintenanceResult;
}

/** Only disjoint machine counts are additive. Contributors are separately paginated. */
export function aggregateWork(rows: readonly MachineWorkMetrics[]) {
  const dates = new Set<string>();
  const zones = new Set<string>();
  for (const row of rows) {
    for (const result of [row.open, row.overdue]) {
      if (result.state !== 'ready') return null;
      if (
        !result.complete ||
        !Number.isSafeInteger(result.value) ||
        result.value! < 0 ||
        result.value !== result.record_count
      )
        throw new InvalidReadResponse('Incomplete work totals');
      const date = result.reporting_date;
      if (!date || !result.timezone)
        throw new InvalidReadResponse('Missing reporting clock');
      dates.add(date);
      zones.add(result.timezone);
    }
  }
  if (
    new Set(rows.map((row) => row.machine)).size !== rows.length ||
    dates.size > 1 ||
    zones.size > 1
  ) {
    throw new InvalidReadResponse('Inconsistent work scope or reporting clock');
  }
  return {
    open: rows.reduce((sum, row) => sum + row.open.value!, 0),
    overdue: rows.reduce((sum, row) => sum + row.overdue.value!, 0),
    machinesWithOpenWork: rows.filter((row) => row.open.value! > 0).length,
    reportingDate: [...dates][0],
    timezone: [...zones][0]
  };
}
