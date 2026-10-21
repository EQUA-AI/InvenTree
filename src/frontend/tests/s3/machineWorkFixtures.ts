import { type Page, type Route, expect } from '@playwright/test';
import type { FaultOccurrence } from '../../src/pages/assets/faults/occurrenceModel';

import { mockMachine } from '../s0/machineFixtures';
import { mockWork } from '../s0/workFixtures';

const recordedAt = '2026-10-18T12:00:00Z';
const PRIORITIES = ['high', 'medium', 'low'] as const;
const OPEN = ['planned', 'ready', 'in_progress', 'on_hold', 'verifying'];

/**
 * Synthetic open jobs for machine 18 at the R6 20/50-job scale fixtures. They
 * are review workloads, not a product cap or any plant's real work count.
 */
export function openJobs(count: number) {
  return Array.from({ length: count }, (_, index) => {
    const id = 73 + index;
    return {
      id,
      reference: `WO-${id}`,
      title:
        index === 1
          ? `Synthetic job ${id} with a long operational title that has to wrap across two readable lines`
          : `Synthetic job ${id}`,
      description: '',
      status: 'backlog',
      priority: PRIORITIES[index % 3],
      due_date: index % 4 === 0 ? '2026-10-25' : null,
      assignee: index % 5 === 2 ? 'Legacy crew' : '',
      machine: 18,
      machine_name: 'Synthetic pump',
      machine_location: '',
      assigned_to: index % 5 === 0 ? 11 : null,
      assigned_to_username: index % 5 === 0 ? 'synthetic.tech' : null,
      assigned_to_name: index % 5 === 0 ? 'Synthetic Technician' : null,
      scheduled_start: index % 2 === 0 ? recordedAt : null,
      scheduled_end: index % 2 === 0 ? '2026-10-18T16:00:00Z' : null,
      estimated_minutes: null,
      work_order_type: 'corrective',
      lifecycle_status: OPEN[index % OPEN.length],
      lifecycle_version: 1,
      attention_reasons: null,
      affected_component: index % 3 === 0 ? 'Drive-end bearing' : '',
      affected_component_ref: index % 3 === 0 ? 'PS-MOTOR-DE-BRG:1' : '',
      actual_started_at: null,
      actual_completed_at: null,
      tags: [],
      company: '',
      company_contact_name: '',
      company_contact_phone: '',
      job_number: '',
      service_quote: '',
      is_active: true,
      created_at: recordedAt,
      updated_at: recordedAt,
      parts: []
    };
  });
}

export const completedRecords = [
  {
    pk: 301,
    machine: 18,
    date: '2026-09-02',
    summary: 'Drive-end bearing replaced',
    details: '',
    performed_by: 'Synthetic crew',
    work_order: 60,
    work_order_reference: 'WO-60',
    work_order_title: 'Replace drive-end bearing',
    work_order_type: 'corrective',
    work_order_component: 'Drive-end bearing',
    lifecycle_status: 'completed',
    actual_completed_at: '2026-09-02T15:00:00Z',
    downtime_minutes: 95,
    verified: true,
    follow_up_required: true,
    amended: false,
    created_at: recordedAt,
    updated_at: recordedAt
  },
  {
    pk: 302,
    machine: 18,
    date: '2025-03-02',
    summary: 'Legacy service visit',
    details: '',
    performed_by: '',
    work_order: null,
    work_order_reference: null,
    work_order_title: null,
    work_order_type: null,
    work_order_component: null,
    lifecycle_status: null,
    actual_completed_at: null,
    downtime_minutes: null,
    verified: false,
    follow_up_required: false,
    amended: false,
    created_at: recordedAt,
    updated_at: recordedAt
  }
];

export interface MachineWorkOptions {
  jobs?: number;
  /** null: defensive unavailable projection; unresolved endpoint scope is 404. */
  open?: number | null;
  completed?: number;
  summaryStatus?: number;
  listStatus?: number;
}

/**
 * Machine 18 with its Maintenance tab's three reads: the work summary, the
 * native paginated Work list (open set only) and the native maintenance
 * records. Every request is recorded so tests can check what was asked.
 */
export async function mockMachineWork(
  page: Page,
  options: MachineWorkOptions = {}
) {
  const jobs = openJobs(options.jobs ?? 20);
  const requests = {
    summary: [] as URL[],
    list: [] as URL[],
    records: [] as URL[]
  };
  // Work-order pages for the navigation cases; registered first so the
  // machine's own routes below take precedence.
  await mockWork(page);
  await mockMachine(page);
  await page.route(
    '**/api/assets/machines/18/work-summary/',
    (route: Route) => {
      requests.summary.push(new URL(route.request().url()));
      if (options.summaryStatus && options.summaryStatus !== 200)
        return route.fulfill({
          status: options.summaryStatus,
          json: { detail: 'Synthetic unavailable source' }
        });
      return route.fulfill({
        json: {
          rule: 'machine_work_views.v1',
          machine: 18,
          open_statuses: OPEN,
          open:
            options.open === null
              ? null
              : {
                  count: options.open ?? jobs.length,
                  highest_priority: jobs.length ? 'high' : null
                },
          completed: {
            count: options.completed ?? completedRecords.length,
            source: 'maintenance records'
          }
        }
      });
    }
  );
  await page.route('**/api/kanban/work-orders/list/**', (route: Route) => {
    const url = new URL(route.request().url());
    requests.list.push(url);
    if (options.listStatus && options.listStatus !== 200)
      return route.fulfill({
        status: options.listStatus,
        json: { detail: 'Synthetic unavailable source' }
      });
    const search = (url.searchParams.get('search') ?? '').toLowerCase();
    const rows = jobs.filter(
      (job) =>
        !search ||
        job.reference.toLowerCase().includes(search) ||
        job.title.toLowerCase().includes(search)
    );
    const offset = Number(url.searchParams.get('offset') ?? 0);
    const limit = Number(url.searchParams.get('limit') ?? 25);
    return route.fulfill({
      json: {
        count: rows.length,
        results: rows.slice(offset, offset + limit),
        next: offset + limit < rows.length ? 'next' : null,
        previous: null
      }
    });
  });
  await page.route('**/api/assets/maintenance/**', (route: Route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === 'OPTIONS')
      return route.fulfill({ json: { actions: {} } });
    requests.records.push(url);
    return route.fulfill({
      json: { count: completedRecords.length, results: completedRecords }
    });
  });
  return requests;
}

export async function openMaintenance(
  page: Page,
  options: { query?: string; scheme?: 'light' | 'dark'; role?: string } = {}
) {
  const entry = `/machines/machine/18/maintenance/${options.query ?? ''}`;
  const query = new URLSearchParams({
    surface: 'machine',
    role: options.role ?? 'viewer',
    scheme: options.scheme ?? 'light',
    entry
  });
  await page.goto(`/playwright/s0-native.html?${query}`);
  await expect(page.getByTestId('machine-maintenance')).toBeVisible();
}

export const occurrences = [
  {
    id: 902,
    external_id: '',
    alarm_code: '',
    severity: 'warning',
    status: 'open',
    active: true,
    title: 'Discharge pressure below its warning limit',
    evidence_summary:
      'Discharge pressure 1.8 bar against a 2.0 bar warning limit.',
    metrics: { peak: 1.8, threshold: 2 },
    detector: 'threshold',
    detector_version: '3',
    source: { id: 5, name: 'Synthetic SCADA', type: 'scada' },
    signals: [
      {
        binding_id: 101,
        display_name: 'Discharge pressure',
        unit: 'bar',
        signal_kind: 'pressure'
      }
    ],
    components: [],
    first_observed_at: '2026-10-19T06:10:00Z',
    last_observed_at: '2026-10-19T09:40:00Z',
    display_shifted: false,
    display_shift_seconds: 0,
    acknowledged_at: null,
    acknowledged_by_name: null,
    resolved_at: null,
    resolution_note: '',
    work: [
      {
        id: 73,
        reference: 'WO-73',
        title: 'Synthetic job 73',
        lifecycle_status: 'in_progress',
        priority: 'high',
        work_order_type: 'corrective',
        affected_component: '',
        actual_completed_at: null,
        relation: 'owning'
      }
    ],
    repair_packet: null,
    snapshot_count: 0,
    snapshots: []
  },
  {
    id: 901,
    external_id: 'ALM-77812',
    alarm_code: 'TT-HI',
    severity: 'critical',
    status: 'resolved',
    active: false,
    title: 'Drive-end bearing high temperature',
    evidence_summary:
      'Bearing temperature 118.4 degC above its 110 degC limit.',
    metrics: {
      value: 118.4,
      unit: 'degC',
      warn_min: null,
      warn_max: 110,
      critical_min: null,
      critical_max: 130,
      vote_confirmed: 2,
      vote_minimum: 2,
      vote_group: 'thermal',
      unchanged_for_seconds: 840
    },
    detector: 'source_alarm',
    detector_version: '',
    source: { id: 5, name: 'Synthetic SCADA', type: 'scada' },
    signals: [
      {
        binding_id: 100,
        display_name: 'DE bearing temperature',
        unit: 'degC',
        signal_kind: 'temperature'
      }
    ],
    components: [
      { code: 'PS-MOTOR-DE-BRG:1', name: 'Drive-end bearing', status: 'draft' }
    ],
    first_observed_at: '2026-10-12T02:14:05Z',
    last_observed_at: '2026-10-12T05:51:40Z',
    display_shifted: false,
    display_shift_seconds: 0,
    acknowledged_at: '2026-10-12T02:20:00Z',
    acknowledged_by_name: 'Synthetic Supervisor',
    resolved_at: '2026-10-12T06:00:00Z',
    resolution_note: 'Reading back inside its limits.',
    work: [
      {
        id: 86,
        reference: 'WO-86',
        title: 'Synthetic job 86',
        lifecycle_status: 'completed',
        priority: 'high',
        work_order_type: 'corrective',
        affected_component: 'Drive-end bearing',
        actual_completed_at: '2026-10-13T10:00:00Z',
        relation: 'owning'
      }
    ],
    repair_packet: { id: 901, reference: 'RP-901', status: 'closed' },
    snapshot_count: 1,
    snapshots: [
      {
        id: 'a3f1c2d4-0000-4000-8000-000000000001',
        signal_label: 'DE bearing temperature',
        unit: 'degC',
        binding: 100,
        source_name: 'Synthetic SCADA',
        captured_by_name: 'Synthetic Technician',
        system_actor: 'historian-import',
        window_start: '2026-10-12T02:14:05Z',
        window_end: '2026-10-12T02:14:05Z',
        captured_at: '2026-10-12T02:16:00Z',
        quality: 'good',
        stale: false,
        reason: 'anomaly_repair',
        content_hash: 'c0ffee'.repeat(10),
        display_shifted: false,
        display_shift_seconds: 0,
        sample_count: 1
      }
    ]
  },
  {
    id: 900,
    external_id: 'ALM-77001',
    alarm_code: 'VT-HI',
    severity: 'warning',
    status: 'acknowledged',
    active: true,
    title: 'Vibration above its warning limit',
    evidence_summary: '',
    metrics: {},
    detector: 'source_alarm',
    detector_version: '',
    source: { id: 5, name: 'Synthetic SCADA', type: 'scada' },
    signals: [],
    components: [],
    first_observed_at: '2026-10-10T08:00:00Z',
    last_observed_at: '2026-10-10T08:00:00Z',
    display_shifted: false,
    display_shift_seconds: 0,
    acknowledged_at: '2026-10-10T08:05:00Z',
    acknowledged_by_name: 'Synthetic Operator',
    resolved_at: null,
    resolution_note: '',
    work: [],
    repair_packet: null,
    snapshot_count: 0,
    snapshots: []
  }
];

export function occurrenceDetail(id: number) {
  const row = occurrences.find((item) => item.id === id)!;
  return {
    rule: 'fault_occurrences.v1',
    machine: 18,
    ...row,
    snapshots: row.snapshots.map((snapshot) => {
      const { sample_count: _count, ...rest } = snapshot;
      return {
        ...rest,
        samples: [{ observed_at: snapshot.window_start, value: 118.4 }],
        statistics: { available: true, latest: 118.4 },
        source_references: {}
      };
    })
  };
}

export const rollup = {
  window_days: 365,
  declared: { source: 'operator-declared profile', fault_codes: ['TT-HI'] },
  observed: {
    maintenance: {
      source: 'maintenance records in the window',
      count: 2,
      first_date: '2026-03-02',
      last_date: '2026-09-02',
      gap_days: { min: 184, max: 184, median: 184 },
      repeat_window_flag: false,
      repeat_window_count: 0
    },
    failure_codes: { source: 'approved repair scopes', top: [] },
    verified_causes: {
      source: 'verified closeouts',
      top: [{ cause: 'bearing wear', count: 1 }]
    }
  }
};

/** Machine 18's Fault History reads: the rollup, occurrences and details. */
export async function mockFaultHistory(
  page: Page,
  options: {
    listStatus?: number;
    empty?: boolean;
    rows?: FaultOccurrence[];
  } = {}
) {
  const requests = { list: [] as URL[], detail: [] as URL[] };
  await mockWork(page);
  await mockMachine(page);
  await page.route('**/api/assets/machines/18/fault-history/', (route) =>
    route.fulfill({ json: rollup })
  );
  await page.route(
    '**/api/assets/machines/18/fault-history/occurrences/?*',
    (route) => {
      const url = new URL(route.request().url());
      requests.list.push(url);
      if (options.listStatus && options.listStatus !== 200)
        return route.fulfill({
          status: options.listStatus,
          json: { detail: 'Synthetic unavailable source' }
        });
      const status = url.searchParams.get('status') ?? 'all';
      const rows = options.empty
        ? []
        : (options.rows ?? occurrences).filter((row) =>
            status === 'active'
              ? row.active
              : status === 'resolved'
                ? row.status === 'resolved'
                : true
          );
      return route.fulfill({
        json: {
          rule: 'fault_occurrences.v1',
          plant_timezone: null,
          machine: 18,
          status,
          count: rows.length,
          limit: 25,
          offset: Number(url.searchParams.get('offset') ?? 0),
          results: rows.slice(
            Number(url.searchParams.get('offset') ?? 0),
            Number(url.searchParams.get('offset') ?? 0) + 25
          )
        }
      });
    }
  );
  await page.route(
    /\/api\/assets\/machines\/18\/fault-history\/occurrences\/\d+\/$/,
    (route) => {
      const url = new URL(route.request().url());
      requests.detail.push(url);
      const id = Number(url.pathname.split('/').at(-2));
      if (!(options.rows ?? occurrences).some((row) => row.id === id))
        return route.fulfill({ status: 404, json: { detail: 'Not found' } });
      const row = options.rows?.find((item) => item.id === id);
      const detail = row
        ? {
            ...row,
            snapshots: row.snapshots.map((snapshot) => ({
              ...snapshot,
              samples: [{ observed_at: snapshot.window_start, value: 118.4 }],
              statistics: { available: true, latest: 118.4 },
              source_references: {}
            }))
          }
        : occurrenceDetail(id);
      return route.fulfill({ json: detail });
    }
  );
  return requests;
}

export async function openFaultHistory(
  page: Page,
  options: { query?: string; scheme?: 'light' | 'dark'; basename?: string } = {}
) {
  const query = new URLSearchParams({
    surface: 'machine',
    role: 'viewer',
    scheme: options.scheme ?? 'light',
    basename: options.basename ?? '',
    entry: `/machines/machine/18/fault-history/${options.query ?? ''}`
  });
  await page.goto(`/playwright/s0-native.html?${query}`);
  await expect(page.getByTestId('fault-occurrences')).toBeVisible();
}
