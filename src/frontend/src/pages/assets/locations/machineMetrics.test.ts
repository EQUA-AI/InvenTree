import type { MachineHealthSummary } from '@lib/types/MachineHealth';
import type { AxiosInstance } from 'axios';
import { describe, expect, it, vi } from 'vitest';
import type { MaintenanceResult } from '../../../components/dashboard/widgets/maintenanceMetricsRead';
import {
  aggregateWork,
  healthCounts,
  machineScopeHref,
  mapMachines,
  parseHealthSummary,
  parseMachineScope,
  readLocationMachines
} from './machineMetrics';

const health = (
  patch: Partial<MachineHealthSummary> = {}
): MachineHealthSummary => ({
  state: 'normal',
  configured: true,
  signal_count: 2,
  stale_signal_count: 0,
  degraded_data: false,
  last_observed_at: null,
  anomaly_counts: { info: 0, warning: 0, critical: 0 },
  active_anomaly_count: 0,
  sources: [],
  ...patch
});
const work = (value: number, patch: Partial<MaintenanceResult> = {}) =>
  ({
    state: 'ready',
    complete: true,
    value,
    record_count: value,
    records: [],
    reporting_date: '2026-10-22',
    timezone: 'America/Chicago',
    ...patch
  }) as MaintenanceResult;

describe('production machine scope', () => {
  it('validates locations and retains direct scope in links', () => {
    const scope = parseMachineScope(
      new URLSearchParams('location=12&scope=direct&unused=old')
    );
    expect(scope).toEqual({ location: 12, direct: true });
    expect(machineScopeHref('/maintenance/board/', scope)).toBe(
      '/maintenance/board/?location=12&scope=direct'
    );
    expect(
      parseMachineScope(new URLSearchParams('location=9007199254740992'))
        .location
    ).toBeNull();
    expect(
      parseMachineScope(new URLSearchParams('location=0')).location
    ).toBeNull();
  });
  it('traverses all authorized pages independently of table search', async () => {
    const get = vi
      .fn()
      .mockResolvedValueOnce({
        data: { count: 2, results: [{ pk: 1 }], next: 'next' }
      })
      .mockResolvedValueOnce({
        data: { count: 2, results: [{ pk: 2 }], next: null }
      });
    const result = await readLocationMachines(
      { get } as unknown as AxiosInstance,
      12,
      false,
      new AbortController().signal
    );
    expect(result.map((r) => r.pk)).toEqual([1, 2]);
    expect(get.mock.calls[1][1].params).toEqual({
      location: 12,
      include_descendants: false,
      limit: 100,
      offset: 1
    });
  });
  it('rejects truncated, repeated and changing populations', async () => {
    for (const page of [
      { count: 2, results: [{ pk: 1 }], next: null },
      { count: 1, results: [], next: 'next' }
    ]) {
      await expect(
        readLocationMachines(
          {
            get: vi.fn().mockResolvedValue({ data: page })
          } as unknown as AxiosInstance,
          1,
          true,
          new AbortController().signal
        )
      ).rejects.toThrow();
    }
  });
  it('keeps active-only metric populations aligned with inventory filters', async () => {
    const get = vi
      .fn()
      .mockResolvedValue({ data: { count: 0, results: [], next: null } });
    await readLocationMachines(
      { get } as unknown as AxiosInstance,
      12,
      true,
      new AbortController().signal,
      { activeOnly: true }
    );
    expect(get.mock.calls[0][1].params.active).toBe(true);
    await readLocationMachines(
      { get } as unknown as AxiosInstance,
      12,
      true,
      new AbortController().signal,
      { activeOnly: false }
    );
    expect(get.mock.calls[1][1].params).not.toHaveProperty('active');
  });
  it('cancels before issuing another placement read', async () => {
    const controller = new AbortController();
    controller.abort();
    const get = vi.fn();
    await expect(
      readLocationMachines(
        { get } as unknown as AxiosInstance,
        1,
        true,
        controller.signal
      )
    ).rejects.toThrow();
    expect(get).not.toHaveBeenCalled();
  });
});

describe('health semantics', () => {
  it('retains critical state caused by anomalies even with stale signals', () => {
    const summary = parseHealthSummary(
      health({ state: 'critical', stale_signal_count: 2, degraded_data: true })
    );
    expect(
      healthCounts([summary, health({ state: 'unknown', configured: false })])
    ).toEqual({
      critical: 1,
      unknown: 1,
      unconfigured: 1,
      degraded: 1,
      stale: 1
    });
  });
  it('rejects malformed health instead of treating it as unconfigured', () => {
    expect(() => parseHealthSummary({} as MachineHealthSummary)).toThrow();
  });
});

describe('authoritative work totals', () => {
  it('sums counts independently of contributor pages and counts distinct machines', () => {
    expect(
      aggregateWork([
        { machine: 1, open: work(31), overdue: work(4) },
        { machine: 2, open: work(0), overdue: work(0) }
      ])
    ).toMatchObject({
      open: 31,
      overdue: 4,
      machinesWithOpenWork: 1,
      reportingDate: '2026-10-22',
      timezone: 'America/Chicago'
    });
  });
  it('does not sum incomplete reads or across reporting-date boundaries', () => {
    expect(() =>
      aggregateWork([
        { machine: 1, open: work(2, { complete: false }), overdue: work(0) }
      ])
    ).toThrow();
    expect(() =>
      aggregateWork([
        {
          machine: 1,
          open: work(2),
          overdue: work(0, { reporting_date: '2026-10-23' })
        }
      ])
    ).toThrow();
    expect(
      aggregateWork([
        {
          machine: 1,
          open: work(2, { state: 'unavailable' }),
          overdue: work(0)
        }
      ])
    ).toBeNull();
  });
  it('rejects repeated machine counts and distinguishes zero from unavailable', () => {
    const row = { machine: 1, open: work(0), overdue: work(0) };
    expect(() => aggregateWork([row, row])).toThrow();
    expect(aggregateWork([row])?.open).toBe(0);
  });
});

it('bounds concurrent reads and preserves input order', async () => {
  let active = 0;
  let peak = 0;
  const result = await mapMachines(
    [1, 2, 3, 4, 5, 6],
    new AbortController().signal,
    async (value) => {
      active++;
      peak = Math.max(peak, active);
      await new Promise((resolve) => setTimeout(resolve, 1));
      active--;
      return value * 2;
    },
    2
  );
  expect(peak).toBe(2);
  expect(result).toEqual([2, 4, 6, 8, 10, 12]);
});

it('never returns a partial successful aggregate after a member fails', async () => {
  await expect(
    mapMachines([1, 2], new AbortController().signal, async (id) => {
      if (id === 2) throw new Error('denied');
      return id;
    })
  ).rejects.toThrow('denied');
});
