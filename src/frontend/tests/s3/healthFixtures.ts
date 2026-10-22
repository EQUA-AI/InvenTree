import { type Page, type Route, expect } from '@playwright/test';
import { mockTheme } from './themeFixtures';

import type {
  HealthEvidenceSnapshot,
  HealthState,
  MachineAnomaly,
  MachineSignal,
  SignalQuality,
  SignalTrend
} from '../../lib/types/MachineHealth';
import { mockMachine } from '../s0/machineFixtures';

/**
 * The native Machine Health tab for one pump, with every Health read it makes
 * answered from these fixtures. Trend samples are built from the window each
 * request names, so the table's batch and the full chart see the same record.
 */

export const MACHINE = 18;
/** The page clock: every relative time on the tab is measured from here. */
export const NOW = Date.parse('2026-10-18T09:00:00Z');
/** Server-default expected interval; fixtures also measure their own gaps. */
export const CADENCE_SECONDS = 5;

/**
 * The shape of one signal's history over a requested window.
 *
 * - `steady` reads every five seconds.
 * - `gappy` reads every five seconds for three minutes, with one non-numeric
 *   sample, then is silent for two minutes, then reads again to the end.
 * - `zero` is a recorded zero: a stopped flow, drawn at zero, never a gap.
 * - `empty` is a successful read that held nothing for this signal.
 * - `missing` is absent from the batch the table asked for.
 * - a reason is a source that could not serve history at all.
 */
export type TrendShape =
  | 'steady'
  | 'gappy'
  | 'zero'
  | 'empty'
  | 'missing'
  | 'sparse'
  | 'flapping'
  | 'two-readings'
  | 'close-pair'
  | 'bad'
  | 'mostly-bad'
  | {
      reason:
        | 'NO_CONNECTOR'
        | 'SOURCE_UNAVAILABLE'
        | 'SOURCE_TIMEOUT'
        | 'HISTORY_UNSUPPORTED';
    };

export interface HealthSignalSpec {
  active?: boolean;
  criticalDesignation?: MachineSignal['critical_designation'];
  observedAt?: string | null;
  freshnessSeconds?: number;
  component?: MachineSignal['component'];
  id: number;
  key: string;
  name: string;
  unit: string;
  kind?: string;
  value: number | string | null;
  state?: HealthState;
  quality?: SignalQuality;
  stale?: boolean;
  limits?: Partial<
    Record<'warn_min' | 'warn_max' | 'critical_min' | 'critical_max', number>
  >;
  trend?: TrendShape;
}

/** A pump whose record has a silence, a recorded zero and a raw channel. */
export const HEALTH_SIGNALS: readonly HealthSignalSpec[] = [
  {
    id: 201,
    key: '/pd/P1/dv',
    name: 'Discharge flow',
    unit: 'cusec',
    kind: 'flow',
    value: 812.5,
    state: 'normal',
    limits: { warn_min: 600, critical_min: 400 },
    trend: 'gappy'
  },
  {
    id: 202,
    key: '/dex/PUMP1_BYPASS_FLOW',
    name: 'Bypass flow',
    unit: 'm3/h',
    kind: 'flow',
    value: 0,
    trend: 'zero'
  },
  {
    id: 203,
    key: '/dex/PUMP1_MOTOR_DE_VIBRATION_RAW',
    name: 'Motor DE vibration (raw)',
    unit: '',
    kind: 'vibration',
    value: 1834,
    trend: 'steady'
  },
  {
    id: 204,
    key: '/dex/PUMP1_SEAL_FLUSH_FLOW',
    name: 'Seal flush flow',
    unit: 'l/min',
    kind: 'flow',
    value: 12.1,
    trend: 'empty'
  },
  {
    id: 205,
    key: '/dex/PUMP1_COOLING_WATER_FLOW',
    name: 'Cooling water flow',
    unit: 'm3/h',
    kind: 'flow',
    value: 31.4,
    trend: { reason: 'NO_CONNECTOR' }
  },
  {
    id: 206,
    key: '/dex/PUMP1_WINDING_TEMPERATURE',
    name: 'Winding temperature',
    unit: 'degC',
    kind: 'temperature',
    value: 84.2,
    state: 'normal',
    limits: { warn_max: 125, critical_max: 145 },
    trend: 'missing'
  }
];

/** One sample of the fixture record. */
interface FixtureSample {
  observed_at: string;
  value: number | string | boolean | null;
  quality: SignalQuality;
}

const iso = (ms: number) => new Date(ms).toISOString();

/** Every five seconds from `from` to `to`, inclusive of the end. */
function every(from: number, to: number, value: (at: number) => number) {
  const samples: FixtureSample[] = [];
  const first = Math.ceil(from / 5000) * 5000;
  for (let at = first; at <= to; at += CADENCE_SECONDS * 1000) {
    samples.push({ observed_at: iso(at), value: value(at), quality: 'good' });
  }
  return samples;
}

/** The record one signal holds for `[from, to]`, by its shape. */
export function recordFor(
  spec: HealthSignalSpec,
  from: number,
  to: number
): FixtureSample[] {
  const base = typeof spec.value === 'number' ? spec.value : 1;
  const wave = (at: number) =>
    Number((base + Math.sin(at / 47_000) * base * 0.02).toFixed(3));
  switch (spec.trend) {
    case 'sparse':
    case 'flapping':
    case 'two-readings': {
      const offsets =
        spec.trend === 'sparse'
          ? [0, 5, 595]
          : spec.trend === 'flapping'
            ? [0, 5, 65, 70, 130, 135, 195]
            : [0, 60];
      return offsets.map((seconds) => ({
        observed_at: iso(from + seconds * 1000),
        value: base,
        quality: 'good'
      }));
    }
    case 'close-pair':
      return [0, 1, 61, 121, 181, 241, 301].map((seconds) => ({
        observed_at: iso(from + seconds * 1000),
        value: base,
        quality: 'good'
      }));
    case 'bad':
    case 'mostly-bad':
      return every(from, to, wave).map((sample, index) => ({
        ...sample,
        quality: spec.trend === 'mostly-bad' && index === 0 ? 'good' : 'bad'
      }));
    case 'gappy': {
      // Three minutes of readings, then two minutes of silence, then the rest.
      const firstRun = every(to - 600_000, to - 420_000, wave);
      const notNumeric = firstRun.find(
        (sample) => sample.observed_at === iso(to - 500_000)
      );
      if (notNumeric) {
        notNumeric.value = null;
      }
      return [...firstRun, ...every(to - 300_000, to, wave)].filter(
        (sample) => {
          const at = Date.parse(sample.observed_at);
          return at >= from && at <= to;
        }
      );
    }
    case 'zero':
      return every(from, to, () => 0);
    case 'empty':
      return [];
    default:
      return every(from, to, wave);
  }
}

/** The trend envelope the server returns for one binding and window. */
export function trendFor(
  spec: HealthSignalSpec,
  from: number,
  to: number,
  options: { cadence?: boolean } = {}
) {
  const envelope = {
    binding_id: spec.id,
    display_name: spec.name,
    unit: spec.unit,
    signal_kind: spec.kind ?? '',
    source_id: 1,
    source_name: 'Fixture historian',
    source_type: 'scada',
    window_start: iso(from),
    window_end: iso(to),
    max_samples: 4320,
    display_shifted: false,
    display_shift_seconds: 0,
    limits: {
      max_window_seconds: 21600,
      max_samples: 4320,
      ...(options.cadence === false
        ? {}
        : { expected_sample_interval_seconds: CADENCE_SECONDS })
    }
  };
  if (typeof spec.trend === 'object') {
    return {
      ...envelope,
      available: false,
      reason: spec.trend.reason,
      detail: {
        NO_CONNECTOR:
          'This source has no configured connector to read history from.',
        HISTORY_UNSUPPORTED: 'This source does not support historical windows.',
        SOURCE_TIMEOUT: 'The source did not answer within the read timeout.',
        SOURCE_UNAVAILABLE: 'The source could not be reached for this window.'
      }[spec.trend.reason],
      samples: []
    };
  }
  const samples = recordFor(spec, from, to);
  const gaps = samples
    .slice(1)
    .map(
      (sample, index) =>
        (Date.parse(sample.observed_at) -
          Date.parse(samples[index].observed_at)) /
        1000
    )
    .filter((gap) => gap > 0)
    .sort((a, b) => a - b);
  const median = (values: number[]) =>
    values.length % 2
      ? values[Math.floor(values.length / 2)]
      : (values[values.length / 2 - 1] + values[values.length / 2]) / 2;
  const cadence = gaps.length
    ? median(gaps.filter((gap) => gap <= median(gaps)))
    : null;
  return {
    ...envelope,
    available: true,
    truncated: false,
    cadence_seconds:
      options.cadence === false || samples.length < 3 ? null : cadence,
    samples
  };
}

/** The signals endpoint's row for one spec. */
export function signalRow(spec: HealthSignalSpec) {
  return {
    active: spec.active ?? true,
    critical_designation: spec.criticalDesignation ?? null,
    binding_id: spec.id,
    component: spec.component ?? null,
    source_id: 1,
    source_name: 'Fixture historian',
    source_type: 'scada',
    external_key: spec.key,
    display_name: spec.name,
    signal_kind: spec.kind ?? '',
    unit: spec.unit,
    value: spec.value,
    observed_at:
      spec.observedAt !== undefined
        ? spec.observedAt
        : spec.value === null
          ? null
          : iso(NOW - 20_000),
    received_at: spec.value === null ? null : iso(NOW - 18_000),
    quality: spec.quality ?? (spec.value === null ? 'unknown' : 'good'),
    stale: spec.stale ?? false,
    freshness_threshold_seconds: spec.freshnessSeconds ?? 300,
    state: spec.state ?? 'unknown',
    limits: {
      normal_min: null,
      normal_max: null,
      warn_min: spec.limits?.warn_min ?? null,
      warn_max: spec.limits?.warn_max ?? null,
      critical_min: spec.limits?.critical_min ?? null,
      critical_max: spec.limits?.critical_max ?? null
    }
  };
}

export interface HealthFixtureOptions {
  recordedTelemetryAsLive?: boolean;
  signals?: readonly HealthSignalSpec[];
  scheme?: 'light' | 'dark';
  nativeTheme?: boolean;
  compactChrome?: boolean;
  /** Overrides on the summary the tab opens with. */
  summary?: Record<string, unknown>;
  anomalies?: Partial<MachineAnomaly>[];
  pastAnomalies?: Partial<MachineAnomaly>[];
  /** Omit the cadence fields, as a server before this change would. */
  cadence?: boolean;
  /** Fail the table's batch read. */
  batchStatus?: number;
  /** Fail the full chart's read. */
  chartStatus?: number;
  chartOverrides?: Partial<SignalTrend>;
  chartHandler?: (route: Route, url: URL) => Promise<void>;
  dataRange?: Record<string, unknown>;
  dataRangeStatus?: number;
  /** Hold extent metadata independently of current and selected trend reads. */
  dataRangeReady?: Promise<void>;
  snapshots?: HealthEvidenceSnapshot[];
  snapshotsStatus?: number;
  anomaliesStatus?: number;
  signalsStatus?: number;
  /** Extra query string on the Health entry. */
  entrySearch?: string;
  /** Leave the page on the real clock. */
  realClock?: boolean;
}

/** Every request the tab made, for asserting what was read. */
export interface HealthRequests {
  batch: URL[];
  chart: URL[];
}

/** An anomaly record with the native fields filled in. */
export function anomaly(
  overrides: Partial<MachineAnomaly> = {}
): MachineAnomaly {
  return {
    pk: 44,
    machine: MACHINE,
    source: 1,
    source_name: 'Fixture historian',
    source_type: 'scada',
    external_id: '',
    alarm_code: '',
    fingerprint: 'fixture',
    severity: 'warning',
    status: 'open',
    title: 'Discharge flow below warning limit',
    evidence_summary:
      'Synthetic discharge flow read 580 cusec against a 600 cusec limit.',
    metrics: {},
    detector: 'threshold',
    detector_version: '1',
    signals: [],
    first_observed_at: iso(NOW - 3_600_000),
    last_observed_at: iso(NOW - 3_000_000),
    display_shift_seconds: 0,
    acknowledged_at: null,
    acknowledged_by_name: null,
    acknowledgement_note: '',
    resolved_at: null,
    resolution_note: '',
    work_order: null,
    repair_packet: null,
    created_at: iso(NOW - 3_600_000),
    updated_at: iso(NOW - 3_000_000),
    ...overrides
  };
}

/**
 * Open the native Health tab through Machine Details with every Health read
 * answered. Returns the trend requests so a test can check what was asked.
 */
export async function openHealth(
  page: Page,
  options: HealthFixtureOptions = {}
): Promise<HealthRequests> {
  await mockTheme(page);
  const specs = options.signals ?? HEALTH_SIGNALS;
  const requests: HealthRequests = { batch: [], chart: [] };
  if (!options.realClock) {
    await page.clock.setFixedTime(new Date(NOW));
  }
  await mockMachine(page);
  const health = `**/api/machine-health/machines/${MACHINE}/health`;
  await page.route(`${health}/`, (route: Route) =>
    route.fulfill({
      json: {
        recorded_telemetry_as_live: options.recordedTelemetryAsLive ?? true,
        state: 'normal',
        configured: true,
        signal_count: specs.filter((spec) => spec.active !== false).length,
        stale_signal_count: specs.filter(
          (spec) => spec.active !== false && spec.stale
        ).length,
        degraded_data: specs.some(
          (spec) => spec.stale || (spec.quality && spec.quality !== 'good')
        ),
        last_observed_at: iso(NOW - 20_000),
        anomaly_counts: { critical: 0, warning: 0, info: 0 },
        active_anomaly_count: 0,
        sources: [
          {
            source_id: 1,
            name: 'Fixture historian',
            source_type: 'scada',
            active: true,
            healthy: true,
            last_success_at: iso(NOW - 15_000),
            last_error_at: null,
            last_error_code: '',
            freshness_threshold_seconds: 300,
            mapped_tag_count: specs.filter((spec) => spec.active !== false)
              .length
          }
        ],
        ...options.summary
      }
    })
  );
  await page.route(`${health}/signals/**`, (route: Route) => {
    const rows = specs.map((spec) => ({
      ...signalRow(spec),
      recorded_telemetry_as_live: options.recordedTelemetryAsLive ?? true
    }));
    return route.fulfill({
      status: options.signalsStatus ?? 200,
      json: { count: rows.length, results: rows }
    });
  });
  await page.route(`${health}/anomalies/**`, (route: Route) => {
    const status = new URL(route.request().url()).searchParams.get('status');
    const rows =
      status === 'resolved'
        ? (options.pastAnomalies ?? []).map((row) => anomaly(row))
        : (options.anomalies ?? []).map((row) => anomaly(row));
    return route.fulfill({
      status: options.anomaliesStatus ?? 200,
      json: { count: rows.length, results: rows }
    });
  });
  await page.route(`${health}/data-range/**`, async (route: Route) => {
    await options.dataRangeReady;
    return route.fulfill({
      status: options.dataRangeStatus ?? 200,
      json: {
        available: true,
        from: iso(NOW - 10 * 86_400_000),
        to: iso(NOW),
        display_shift_seconds: 0,
        recorded_telemetry_as_live: options.recordedTelemetryAsLive ?? true,
        ...options.dataRange
      }
    });
  });
  await page.route(`${health}/trends/**`, (route: Route) => {
    const url = new URL(route.request().url());
    requests.batch.push(url);
    if (options.batchStatus) {
      return route.fulfill({
        status: options.batchStatus,
        json: { detail: 'Fixture batch failure' }
      });
    }
    const from = Date.parse(url.searchParams.get('from') ?? '');
    const to = Date.parse(url.searchParams.get('to') ?? '');
    const wanted = (url.searchParams.get('bindings') ?? '').split(',');
    const results = specs
      .filter(
        (spec) => wanted.includes(String(spec.id)) && spec.trend !== 'missing'
      )
      .map((spec) =>
        trendFor(spec, from, to, { cadence: options.cadence ?? true })
      );
    return route.fulfill({ json: { results } });
  });
  await page.route(`${health}/trend/**`, (route: Route) => {
    const url = new URL(route.request().url());
    requests.chart.push(url);
    if (options.chartHandler) return options.chartHandler(route, url);
    if (options.chartStatus) {
      return route.fulfill({
        status: options.chartStatus,
        json: { detail: 'Fixture chart failure' }
      });
    }
    const spec = specs.find(
      (candidate) => String(candidate.id) === url.searchParams.get('binding')
    );
    const from = Date.parse(url.searchParams.get('from') ?? '');
    const to = Date.parse(url.searchParams.get('to') ?? '');
    return route.fulfill({
      json: spec
        ? {
            ...trendFor(
              {
                ...spec,
                trend: spec.trend === 'missing' ? 'steady' : spec.trend
              },
              from,
              to,
              { cadence: options.cadence ?? true }
            ),
            recorded_telemetry_as_live: options.recordedTelemetryAsLive ?? true,
            ...options.chartOverrides
          }
        : { detail: 'Unknown binding' },
      status: spec ? 200 : 400
    });
  });
  await page.route(`${health}/snapshots/**`, (route: Route) => {
    const id = new URL(route.request().url()).searchParams.get('snapshot');
    const rows = id
      ? (options.snapshots ?? []).filter((row) => row.id === id)
      : (options.snapshots ?? []);
    return route.fulfill({
      status: options.snapshotsStatus ?? (id && !rows.length ? 404 : 200),
      json: {
        count: rows.length,
        results: rows
      }
    });
  });
  const entry = `/machines/machine/${MACHINE}/health/${options.entrySearch ?? ''}`;
  const query = new URLSearchParams({
    surface: options.nativeTheme ? 'machine-shell' : 'machine',
    entry,
    scheme: options.scheme ?? 'light',
    nativeTheme: String(options.nativeTheme ?? false),
    compactChrome: String(options.compactChrome ?? false)
  });
  // Match Performance's diagnostic wrapping, including a reopened canonical
  // entry. A style added only after this goto disappears on the next document.
  await page.addInitScript(() => {
    document.addEventListener('DOMContentLoaded', () => {
      const style = document.createElement('style');
      style.textContent =
        '[data-testid="destination"] { overflow-wrap: anywhere; }';
      document.head.appendChild(style);
    });
  });
  await page.goto(`/playwright/s0-native.html?${query}`);
  await expect(
    page.getByRole('heading', { name: 'Signals', exact: true })
  ).toBeVisible();
  return requests;
}

/** The Signals table on the Health tab. */
export const signalTable = (page: Page) =>
  page.getByRole('table', { name: 'Mapped signal readings', exact: true });

/** The full trend card: the Paper holding the Parameter and Range controls. */
export const trendCard = (page: Page) =>
  page
    .locator('.mantine-Paper-root')
    .filter({ has: page.getByRole('combobox', { name: 'Parameter' }) })
    .last();

/** The row sparkline drawn for one signal. */
export const sparkline = (page: Page, name: string) =>
  page.getByRole('img', { name: `Recent trend for ${name}` });
