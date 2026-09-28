import { describe, expect, it, vi } from 'vitest';
import {
  type DemoHistory,
  clearDeniedDemoQueries,
  conditionCounts,
  coverageCounts,
  demoAvailability,
  demoHref,
  demoQueryKeys,
  historyChartSeries,
  historyWindow,
  isDeniedError,
  parseDemoScope,
  workTruncation
} from './demoMetrics';

function failure(status: number) {
  return { response: { status } };
}

describe('demo query key identity', () => {
  const base = {
    identity: 7,
    session: 'sess-1',
    location: 12,
    descendants: true,
    window: {
      start: '2026-09-10T00:00:00.000Z',
      end: '2026-09-24T00:00:00.000Z'
    }
  };

  it('carries identity, session, location, descendants and window', () => {
    const keys = demoQueryKeys(base);
    expect(keys.metrics).toEqual([
      'demo-metrics',
      7,
      'metrics',
      'sess-1',
      12,
      true,
      '2026-09-10T00:00:00.000Z',
      '2026-09-24T00:00:00.000Z'
    ]);
    expect(keys.work).toEqual([
      'demo-metrics',
      7,
      'work-orders',
      'sess-1',
      12,
      true,
      '2026-09-10T00:00:00.000Z',
      '2026-09-24T00:00:00.000Z'
    ]);
    expect(keys.history).toEqual([
      'demo-metrics',
      7,
      'history',
      'sess-1',
      12,
      true,
      '2026-09-10T00:00:00.000Z',
      '2026-09-24T00:00:00.000Z'
    ]);
  });

  it('never reuses cached data across any scope dimension', () => {
    const original = demoQueryKeys(base).history;
    const originalMetrics = demoQueryKeys(base).metrics;
    const variants = [
      { ...base, identity: 8 },
      { ...base, session: 'sess-2' },
      { ...base, location: 13 },
      { ...base, location: null },
      { ...base, descendants: false },
      {
        ...base,
        window: {
          start: '2026-09-11T00:00:00.000Z',
          end: '2026-09-24T00:00:00.000Z'
        }
      }
    ];
    for (const variant of variants) {
      expect(demoQueryKeys(variant).history).not.toEqual(original);
      // The time window is a cache dimension for every query, not only the
      // explicitly windowed history one.
      expect(demoQueryKeys(variant).metrics).not.toEqual(originalMetrics);
    }
    // Without a window the history key is stable and explicit.
    expect(demoQueryKeys({ ...base, window: null }).history).toContain(null);
    expect(demoQueryKeys({ ...base, window: null }).metrics).toContain(null);
  });
});

describe('availability states', () => {
  it('distinguishes disabled, loading, denied, error, unavailable and ready', () => {
    expect(demoAvailability({ enabled: false })).toBe('disabled');
    expect(demoAvailability({ enabled: true, isPending: true })).toBe(
      'loading'
    );
    expect(
      demoAvailability({
        enabled: true,
        isError: true,
        error: failure(403)
      })
    ).toBe('denied');
    expect(
      demoAvailability({
        enabled: true,
        isError: true,
        error: failure(500)
      })
    ).toBe('error');
    expect(demoAvailability({ enabled: true, available: false })).toBe(
      'unavailable'
    );
    expect(demoAvailability({ enabled: true, available: true })).toBe('ready');
  });

  it('never reads an API failure as an empty result', () => {
    // A failed request must not reach the 'ready'/'unavailable' branches:
    // no fixture fallback, no numeric zero.
    const failed = demoAvailability({
      enabled: true,
      isPending: false,
      isError: true,
      error: failure(503),
      available: undefined
    });
    expect(failed).toBe('error');
    expect(isDeniedError(failure(401))).toBe(true);
    expect(isDeniedError(failure(500))).toBe(false);
    expect(isDeniedError(new Error('network'))).toBe(false);
  });
});

describe('denied cache clearing', () => {
  it('drops cached demo payloads on a denied response', () => {
    const removeQueries = vi.fn();
    expect(clearDeniedDemoQueries({ removeQueries }, failure(403))).toBe(true);
    expect(removeQueries).toHaveBeenCalledOnce();
    const options = removeQueries.mock.calls[0][0];
    expect(options.queryKey).toEqual(['demo-metrics']);
    // Only data-carrying queries are removed; an active errored query has
    // no payload and must survive so nothing refetch-loops.
    expect(options.predicate({ state: { data: { cohort_size: 6 } } })).toBe(
      true
    );
    expect(options.predicate({ state: { data: undefined } })).toBe(false);
    expect(options.predicate({ state: {} })).toBe(false);
  });

  it('keeps the cache for non-denied outcomes', () => {
    const removeQueries = vi.fn();
    expect(clearDeniedDemoQueries({ removeQueries }, failure(500))).toBe(false);
    expect(clearDeniedDemoQueries({ removeQueries }, undefined)).toBe(false);
    expect(removeQueries).not.toHaveBeenCalled();
  });
});

describe('history chart series', () => {
  const history: Pick<DemoHistory, 'daily'> = {
    daily: [
      {
        date: '2026-09-10',
        planned_machine_minutes: 960,
        downtime_machine_minutes: 0,
        contributing_interval_ids: []
      },
      {
        date: '2026-09-11',
        planned_machine_minutes: 0,
        downtime_machine_minutes: null,
        contributing_interval_ids: []
      },
      {
        date: '2026-09-12',
        planned_machine_minutes: 480,
        downtime_machine_minutes: 10,
        contributing_interval_ids: []
      }
    ]
  };

  it('keeps missing coverage as null, never zero', () => {
    const points = historyChartSeries(history);
    expect(points[0].downtime).toBe(0);
    expect(points[1].downtime).toBeNull();
    expect(points[2].downtime).toBe(10);
    expect(points.map((point) => point.planned)).toEqual([960, 0, 480]);
  });

  it('returns an empty series without inventing data', () => {
    expect(historyChartSeries(undefined)).toEqual([]);
    expect(historyChartSeries({ daily: [] })).toEqual([]);
  });
});

describe('breakdowns stay separate', () => {
  it('lists coverage and condition as independent counts', () => {
    const metrics = {
      observation_coverage: { fresh: 3, stale: 1 },
      fresh_condition: { normal: 1, warning: 1, critical: 1 }
    };
    expect(coverageCounts(metrics)).toEqual([
      { state: 'fresh', count: 3 },
      { state: 'partial', count: 0 },
      { state: 'stale', count: 1 },
      { state: 'never_seen', count: 0 },
      { state: 'not_configured', count: 0 }
    ]);
    expect(conditionCounts(metrics)).toEqual([
      { condition: 'normal', count: 1 },
      { condition: 'warning', count: 1 },
      { condition: 'critical', count: 1 }
    ]);
  });
});

describe('filter-preserving drilldowns', () => {
  it('keeps session and location filters in links', () => {
    expect(
      demoHref('/machines/machine/5/', {
        session: 'sess-1',
        location: 12,
        direct: true
      })
    ).toBe('/machines/machine/5/?demo_session=sess-1&location=12&scope=direct');
  });

  it('carries nothing demo-specific without a session', () => {
    expect(demoHref('/maintenance/board/', {})).toBe('/maintenance/board/');
    expect(
      demoHref('/maintenance/board/', { session: null, location: 3 })
    ).toBe('/maintenance/board/?location=3');
  });
});

describe('history window', () => {
  it('aligns to whole UTC days and is deterministic', () => {
    const now = new Date('2026-09-26T07:30:00Z');
    expect(historyWindow(14, now)).toEqual({
      start: '2026-09-12T00:00:00.000Z',
      end: '2026-09-26T00:00:00.000Z'
    });
  });
});

describe('URL scope parsing at drill-down destinations', () => {
  const session = '123e4567-e89b-12d3-a456-426614174000';

  it('reads a valid session, location and direct flag', () => {
    const params = new URLSearchParams({
      demo_session: session,
      location: '12',
      scope: 'direct'
    });
    expect(parseDemoScope(params)).toEqual({
      session,
      location: 12,
      direct: true
    });
  });

  it('ignores malformed values instead of trusting them', () => {
    expect(
      parseDemoScope(
        new URLSearchParams({ demo_session: 'not-a-uuid', location: 'abc' })
      )
    ).toEqual({ session: null, location: null, direct: false });
    expect(parseDemoScope(new URLSearchParams())).toEqual({
      session: null,
      location: null,
      direct: false
    });
  });

  it('keeps descendant mode explicit', () => {
    const params = new URLSearchParams({ demo_session: session });
    expect(parseDemoScope(params).direct).toBe(false);
  });
});

describe('honest truncation of the contributing list', () => {
  const row = {
    id: 1,
    reference: 'WO-1',
    title: 'Inspect pump',
    machine_id: 5,
    priority: 'medium',
    lifecycle_status: 'in_progress',
    is_active: true,
    open: true,
    due_date: null,
    overdue: false
  };

  it('reports bounded rows against the full total', () => {
    expect(
      workTruncation({
        count: 7,
        results_returned: 2,
        has_more: true,
        results: [row, { ...row, id: 2 }]
      })
    ).toEqual({ shown: 2, total: 7 });
  });

  it('never claims truncation for a complete list', () => {
    expect(
      workTruncation({
        count: 1,
        results_returned: 1,
        has_more: false,
        results: [row]
      })
    ).toBeNull();
    expect(
      workTruncation({
        count: 0,
        results_returned: 0,
        has_more: false,
        results: []
      })
    ).toBeNull();
  });

  it('falls back to row counting for older payloads', () => {
    // A payload without the explicit fields still must not present a short
    // list as exhaustive.
    expect(workTruncation({ count: 5, results: [row] })).toEqual({
      shown: 1,
      total: 5
    });
    expect(workTruncation({ count: 1, results: [row] })).toBeNull();
  });
});
