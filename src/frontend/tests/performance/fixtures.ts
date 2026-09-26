import type { Page, Route } from '@playwright/test';

/**
 * Component checks for the Performance tab, against fixtures shaped like a
 * Cedar Creek pump: every value the page shows must come from the fixture,
 * a missing family must be stated rather than faked, and the controls must
 * change what is asked of the server.
 */

export const MACHINE = 18;
export const NOW = Date.parse('2026-09-26T12:00:00Z');

interface SignalSpec {
  key: string;
  value: number | string | null;
  unit: string;
  name: string;
  limits?: Partial<
    Record<'warn_max' | 'critical_max' | 'warn_min' | 'critical_min', number>
  >;
  stale?: boolean;
  state?: string;
  quality?: string;
}

export const SIGNALS: SignalSpec[] = [
  {
    key: '/dex/PUMP1_ACTIVE_POWER',
    value: 8.42,
    unit: 'MW',
    name: 'Active Power'
  },
  {
    key: '/dex/PUMP1_SPEED',
    value: 745,
    unit: 'rpm',
    name: 'Motor Shaft Speed'
  },
  {
    key: '/dex/PUMP1_DISCHARGE_PRESSURE',
    value: 12.4,
    unit: 'mH2O',
    name: 'Discharge Pressure'
  },
  {
    key: '/dex/PUMP1_PUMP_POWERFATCOR',
    value: 0.94,
    unit: '',
    name: 'Power Factor'
  },
  {
    key: '/dex/PUMP1_PUMP_FREQUENCY',
    value: 50.02,
    unit: 'Hz',
    name: 'Operating Frequency'
  },
  {
    key: '/dex/PUMP1_EXCITATION_FLD_VLTG_PROCESS_VALUE',
    value: 112.5,
    unit: 'V',
    name: 'Excitation Field Voltage'
  },
  {
    key: '/dex/PUMP1_EOPD_VALVE_POS_PROCESS_VALUE',
    value: 100.2,
    unit: 'percent',
    name: 'EOPD Valve Position'
  },
  {
    key: '/dex/PUMP1_HOPD_VALVE_POS_PROCESS_VALUE',
    value: 97.2,
    unit: 'percent',
    name: 'HOPD Valve Position'
  },
  { key: '/pd/P1/st', value: 'R', unit: '', name: 'Equipment Status' },
  { key: '/pd/P1/dv', value: 812.5, unit: 'cusec', name: 'dv' },
  ...Array.from({ length: 11 }, (_, i) => ({
    key: `/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED${i + 1}`,
    value: i === 3 ? 3276.7 : 78 + i * 0.4,
    unit: 'degC',
    name: `Motor Winding Temperature ${i + 1}`,
    limits: { warn_max: 125, critical_max: 145 },
    quality: i === 3 ? 'bad' : 'good',
    state: i === 3 ? 'unknown' : 'normal'
  })),
  ...Array.from({ length: 6 }, (_, i) => ({
    key: `/dex/PUMP1_MOTOR_CORE_RTD${i + 1}_PROCESS_VALUE`,
    value: 40 + i * 0.5,
    unit: 'degC',
    name: `Motor Core RTD ${i + 1}`
  })),
  ...Array.from({ length: 4 }, (_, i) => ({
    key: `/dex/PUMP1_PUMP_COOLING_WATER_INLET_TEMP${i + 1}`,
    value: 29 + i * 0.1,
    unit: 'degC',
    name: `Cooling Water Inlet Temperature ${i + 1}`
  })),
  ...Array.from({ length: 4 }, (_, i) => ({
    key: `/dex/PUMP1_PUMP_COOLING_WATER_OUTLET_TEMP${i + 1}`,
    value: 34 + i * 0.2,
    unit: 'degC',
    name: `Cooling Water Outlet Temperature ${i + 1}`
  })),
  ...Array.from({ length: 3 }, (_, i) => ({
    key: `/dex/PUMP1_THRST_BRG_THRST_PD_RTD${i + 1}_PROCESS_VALUE`,
    value: 52 + i,
    unit: 'degC',
    name: `Thrust Bearing Pad RTD ${i + 1}`
  })),
  // A sensor with no current reading: drawn as absent, never as zero.
  {
    key: '/dex/PUMP1_PUMP_THRUST_AXIAL_PAD_2D5',
    value: null,
    unit: 'degC',
    name: 'Thrust Axial Pad 2'
  }
];

export function signalRows() {
  return SIGNALS.map((spec, index) => ({
    binding_id: 100 + index,
    source_id: 1,
    source_name: 'Fixture source',
    source_type: 'scada',
    external_key: spec.key,
    display_name: spec.name,
    signal_kind: '',
    unit: spec.unit,
    value: spec.value,
    observed_at:
      spec.value === null ? null : new Date(NOW - 20_000).toISOString(),
    received_at: new Date(NOW - 10_000).toISOString(),
    quality: spec.quality ?? (spec.value === null ? 'unknown' : 'good'),
    stale: spec.stale ?? false,
    freshness_threshold_seconds: 300,
    state: spec.state ?? (spec.limits ? 'normal' : 'unknown'),
    limits: {
      normal_min: null,
      normal_max: null,
      warn_min: spec.limits?.warn_min ?? null,
      warn_max: spec.limits?.warn_max ?? null,
      critical_min: spec.limits?.critical_min ?? null,
      critical_max: spec.limits?.critical_max ?? null
    }
  }));
}

/** Build a series response for whatever the panel asked for. */
export function seriesFor(url: URL, options: { fail?: boolean } = {}) {
  const from = Date.parse(url.searchParams.get('from') ?? '');
  const to = Date.parse(url.searchParams.get('to') ?? '');
  const points = Number(url.searchParams.get('points') ?? 240);
  const ids = (url.searchParams.get('bindings') ?? '')
    .split(',')
    .filter(Boolean)
    .map(Number);
  const seconds = Math.round((to - from) / 1000);
  const expected = Math.floor(seconds / 5);
  const complete = expected <= 240;
  const count = complete ? expected : points;
  const step = (to - from) / count;
  const rows = signalRows();

  const series = ids
    .map((id) => rows.find((row) => row.binding_id === id))
    .filter((row): row is NonNullable<typeof row> => !!row)
    .map((row) => {
      if (options.fail) {
        return {
          ...envelope(row),
          available: false,
          reason: 'SOURCE_UNAVAILABLE',
          detail: 'The source could not be reached for this window.',
          count: 0,
          samples: []
        };
      }
      const base = typeof row.value === 'number' ? row.value : null;
      const samples = Array.from({ length: count }, (_, i) => {
        const t = Math.round(from + i * step);
        if (row.value === 'R') {
          return {
            t,
            v: i < count / 3 ? 0 : 1,
            q: 'good',
            raw: i < count / 3 ? 'I' : 'R'
          };
        }
        if (base === null) return null;
        // A gap in the middle third of the window, so it can be seen to be one.
        if (i > count * 0.45 && i < count * 0.55) return null;
        const wobble = Math.sin(i / 9) * (Math.abs(base) * 0.02 || 0.1);
        if (row.quality === 'bad') return { t, v: base, q: 'bad' };
        return { t, v: Number((base + wobble).toFixed(3)), q: 'good' };
      }).filter((s): s is NonNullable<typeof s> => s !== null);
      return {
        ...envelope(row),
        available: true,
        count: samples.length,
        samples
      };
    });

  return {
    machine: MACHINE,
    station: { pk: 17, name: 'Fixture station' },
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

  function envelope(row: ReturnType<typeof signalRows>[number]) {
    return {
      binding_id: row.binding_id,
      machine_id: MACHINE,
      external_key: row.external_key,
      display_name: row.display_name,
      unit: row.unit,
      signal_kind: row.signal_kind,
      source_id: 1,
      source_name: 'Fixture source',
      limits: row.limits
    };
  }
}

export async function mount(page: Page, options: { fail?: boolean } = {}) {
  const requests: URL[] = [];
  await page.route(
    `**/api/machine-health/machines/${MACHINE}/health/signals/**`,
    (route: Route) =>
      route.fulfill({ json: { count: SIGNALS.length, results: signalRows() } })
  );
  await page.route(
    `**/api/machine-health/machines/${MACHINE}/health/data-range/**`,
    (route: Route) =>
      route.fulfill({
        json: {
          available: true,
          from: new Date(NOW - 10 * 86400_000).toISOString(),
          to: new Date(NOW).toISOString()
        }
      })
  );
  await page.route(
    `**/api/machine-health/machines/${MACHINE}/health/series/**`,
    (route: Route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      return route.fulfill({ json: seriesFor(url, options) });
    }
  );
  await page.goto('/');
  return requests;
}
