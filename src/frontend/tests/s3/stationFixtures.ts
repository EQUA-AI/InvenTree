import { type Page, type Route, expect } from '@playwright/test';

import { NOW, type SignalSpec, signalRows } from '../performance/fixtures';

/**
 * A pump station for the native StationPerformance view: its own levels,
 * running count and status, and four bays whose per-bay power, flow, status
 * and speed are read by key through the station's one series endpoint - the
 * shape the backend's `series_scope` gives a pumphouse. P10 is listed after
 * P2 by the mimic, as the real one lists bays in key order.
 */
export const STATION = 17;

export interface BaySpec {
  key: string;
  machine: number;
  name: string;
  active: boolean;
  state: string;
  power: number | null;
  powerUnit?: string;
  flow: number | null;
  speed: number | null;
  /** 1 running, 0 idle, -1 fault; null for no status reading. */
  status: number | null;
  /** The source has no history for this bay in the window. */
  noHistory?: boolean;
}

export const BAYS: BaySpec[] = [
  {
    key: 'P1',
    machine: 18,
    name: 'Fixture station / Pump 1',
    active: true,
    state: 'running',
    power: 8.42,
    flow: 812.5,
    speed: 745,
    status: 1
  },
  {
    key: 'P2',
    machine: 19,
    name: 'Fixture station / Pump 2',
    active: true,
    state: 'running',
    power: 8.1,
    flow: 790.2,
    speed: 744,
    status: 1
  },
  {
    key: 'P10',
    machine: 27,
    name: 'Fixture station / Pump 10',
    active: false,
    state: 'idle',
    power: 0,
    flow: 0,
    speed: 0,
    status: 0
  },
  {
    key: 'P3',
    machine: 20,
    name: 'Fixture station / Pump 3',
    active: true,
    state: 'running',
    power: 7.95,
    flow: 776.4,
    speed: 743,
    status: 1
  }
];

export const STATION_SIGNALS: SignalSpec[] = [
  {
    key: '/dex/COMMAN_FORBAY_LEVEL',
    value: 112.4,
    unit: 'm',
    name: 'Forebay Level'
  },
  {
    key: '/dex/SURGEPOOL_LEVEL',
    value: 118.9,
    unit: 'm',
    name: 'Surge Pool Level'
  },
  { key: '/pc', value: 3, unit: '', name: 'Pumps Running' },
  { key: '/st', value: 'R', unit: '', name: 'Station Status' }
];

interface KeySpec {
  key: string;
  unit: string;
  name: string;
  value: number | null;
  stepped?: boolean;
  noHistory?: boolean;
}

function keySpecs(bays: readonly BaySpec[]): KeySpec[] {
  const station: KeySpec[] = STATION_SIGNALS.map((s) => ({
    key: s.key,
    unit: s.unit,
    name: s.name,
    value: s.key === '/st' ? 1 : typeof s.value === 'number' ? s.value : null,
    stepped: s.key === '/pc' || s.key === '/st'
  }));
  return [
    ...station,
    ...bays.flatMap((bay) => {
      const n = bay.key.replace(/^P/, '');
      return [
        {
          key: `/dex/PUMP${n}_ACTIVE_POWER`,
          unit: bay.powerUnit ?? 'MW',
          name: `Pump ${n} Active Power`,
          value: bay.power,
          noHistory: bay.noHistory
        },
        {
          key: `/pd/${bay.key}/dv`,
          unit: 'cusec',
          name: `${bay.key} dv`,
          value: bay.flow,
          noHistory: bay.noHistory
        },
        {
          key: `/pd/${bay.key}/st`,
          unit: '',
          name: `${bay.key} st`,
          value: bay.status,
          stepped: true,
          noHistory: bay.noHistory
        },
        {
          key: `/dex/PUMP${n}_SPEED`,
          unit: 'rpm',
          name: `Pump ${n} Speed`,
          value: bay.speed,
          noHistory: bay.noHistory
        }
      ];
    })
  ];
}

/** A series response for the keys the station view asked for. */
export function stationSeries(
  url: URL,
  bays: readonly BaySpec[],
  options: { fail?: boolean; absentKeys?: string[] } = {}
) {
  const from = Date.parse(url.searchParams.get('from') ?? '');
  const to = Date.parse(url.searchParams.get('to') ?? '');
  const points = Number(url.searchParams.get('points') ?? 240);
  const keys = (url.searchParams.get('keys') ?? '').split(',').filter(Boolean);
  const seconds = Math.round((to - from) / 1000);
  const expected = Math.floor(seconds / 5);
  const complete = expected <= 240;
  const count = complete ? expected : points;
  const step = (to - from) / count;
  const specs = keySpecs(bays);
  const series = keys
    .map((key) => specs.findIndex((s) => s.key === key))
    .filter(
      (index) => index >= 0 && !options.absentKeys?.includes(specs[index].key)
    )
    .map((index) => {
      const spec = specs[index];
      const envelope = {
        binding_id: 500 + index,
        machine_id: STATION,
        external_key: spec.key,
        display_name: spec.name,
        unit: spec.unit,
        signal_kind: '',
        source_id: 1,
        source_name: 'Fixture source',
        limits: {
          normal_min: null,
          normal_max: null,
          warn_min: null,
          warn_max: null,
          critical_min: null,
          critical_max: null
        }
      };
      if (options.fail || spec.noHistory || spec.value === null) {
        return {
          ...envelope,
          available: false,
          reason: 'SOURCE_UNAVAILABLE',
          detail: 'The source could not be reached for this window.',
          count: 0,
          samples: []
        };
      }
      const base = spec.value;
      const samples = Array.from({ length: count }, (_, i) => {
        const t = Math.round(from + i * step);
        if (spec.key === '/pc')
          return { t, v: i < count / 2 ? 2 : 3, q: 'good' };
        if (spec.stepped) return { t, v: base, q: 'good' };
        const wobble = Math.sin(i / 9 + index) * (Math.abs(base) * 0.02 || 0);
        return { t, v: Number((base + wobble).toFixed(3)), q: 'good' };
      });
      return { ...envelope, available: true, count: samples.length, samples };
    });
  return {
    machine: STATION,
    station: { pk: STATION, name: 'Fixture station' },
    window_start: new Date(from).toISOString(),
    window_end: new Date(to).toISOString(),
    window_seconds: seconds,
    mode: complete ? 'complete' : 'sampled',
    slots: complete ? null : points,
    resolution_seconds: complete ? 5 : seconds / points,
    cadence_seconds: complete ? 5 : null,
    expected_documents: expected,
    documents_read: count,
    display_shifted: false,
    display_shift_seconds: 0,
    live: {
      enabled: true,
      last_poll_at: new Date(NOW - 30_000).toISOString(),
      last_error_code: '',
      poll_interval_seconds: 60
    },
    limits: {
      max_window_seconds: 86400,
      complete_read_max_documents: 240,
      min_points: 24,
      max_points: 360,
      expected_sample_interval_seconds: 5
    },
    series
  };
}

/** The bay list the station view reads from the mimic endpoint. */
export function stationMimic(bays: readonly BaySpec[]) {
  return {
    station: STATION,
    name: 'Fixture station',
    alarm: null,
    generated_at: new Date(NOW).toISOString(),
    enabled: true,
    source: { pk: 1, name: 'Fixture source' },
    last_poll_at: new Date(NOW - 30_000).toISOString(),
    last_error_code: '',
    layout: { version: 1, review_status: 'reviewed', elements: [] },
    station_points: {},
    bays: bays.map((bay) => ({
      key: bay.key,
      machine: bay.machine,
      name: bay.name,
      active: bay.active,
      state: bay.state,
      alarm: null,
      points: {}
    })),
    selected_unit: null,
    points: {},
    totals: {},
    alarms: [],
    unconfigured_thresholds: 0
  };
}

/**
 * The native station view in the S3 harness. Only the reads it makes are
 * answered - signals, data range, the mimic bay list and the keyed series -
 * and every series request is returned for inspection.
 */
export async function openStation(
  page: Page,
  options: {
    bays?: readonly BaySpec[];
    scheme?: 'light' | 'dark';
    fail?: boolean;
    delaySeriesMs?: number;
    absentKeys?: string[];
    seriesStatus?: number;
    mimicStatus?: number;
    failSeries?: (url: URL) => boolean;
    displayShift?: number | null;
    entry?: string;
  } = {}
) {
  const bays = options.bays ?? BAYS;
  const requests: URL[] = [];
  await page.route(
    `**/api/machine-health/machines/${STATION}/health/signals/**`,
    (route: Route) => {
      const rows = signalRows(STATION_SIGNALS).map((row) => ({
        ...row,
        binding_id: row.binding_id + 400
      }));
      return route.fulfill({ json: { count: rows.length, results: rows } });
    }
  );
  await page.route(
    `**/api/machine-health/machines/${STATION}/health/data-range/**`,
    (route: Route) =>
      route.fulfill({
        json: {
          available: true,
          ...(options.displayShift === null
            ? {}
            : { display_shift_seconds: options.displayShift ?? 0 }),
          from: new Date(NOW - 10 * 86400_000).toISOString(),
          to: new Date(NOW).toISOString()
        }
      })
  );
  await page.route(
    `**/api/machine-health/station/${STATION}/mimic/**`,
    (route: Route) =>
      route.fulfill({
        status: options.mimicStatus ?? 200,
        json: options.mimicStatus
          ? { detail: 'Synthetic bay list denied' }
          : stationMimic(bays)
      })
  );
  await page.route(
    `**/api/machine-health/machines/${STATION}/health/series/**`,
    async (route: Route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      if (options.failSeries?.(url))
        return route.fulfill({
          status: 503,
          json: { detail: 'Synthetic batch unavailable' }
        });
      if (options.delaySeriesMs)
        await new Promise((resolve) =>
          setTimeout(resolve, options.delaySeriesMs)
        );
      return route.fulfill({
        status: options.seriesStatus ?? 200,
        json: options.seriesStatus
          ? { detail: 'Synthetic history read failed' }
          : {
              ...stationSeries(url, bays, {
                fail: options.fail,
                absentKeys: options.absentKeys
              }),
              display_shifted: !!options.displayShift,
              display_shift_seconds: options.displayShift ?? 0
            }
      });
    }
  );
  const query = new URLSearchParams({
    machine: String(STATION),
    asset: 'pumphouse',
    name: 'Fixture station',
    scheme: options.scheme ?? 'light',
    ...(options.entry ? { entry: options.entry } : {})
  });
  await page.goto(`/playwright/s3-performance.html?${query}`);
  await expect(page.locator('[data-kpi="pumps-running"]')).toBeVisible();
  return requests;
}
