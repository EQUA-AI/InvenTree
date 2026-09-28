/**
 * Demo-metrics UI acceptance tests (work package UI acceptance, plan §18.3.4).
 *
 * SCOPE: these are MOCKED BROWSER RENDERING tests — real components
 * (DemoMetricsPanel, MachineDetail, MaintenanceBoard), real React Router
 * navigation and real React Query caching, with every API response supplied by
 * Playwright route mocks. They are NOT a real backend E2E run: no Django, no
 * database, no applied demo session. A real backend E2E (backend + applied
 * demo-metrics session, disposable DB) is tracked as a parent-side item.
 *
 * Run: npx playwright test --config=playwright.demo-metrics.config.ts
 */
import { type Page, expect, test } from '@playwright/test';

const SESSION_ID = '123e4567-e89b-12d3-a456-426614174000';
const SESSION_KEY = 'equa-demo-1';

let failMode: 'none' | 'denied' | 'error' = 'none';
let historyMode: 'nulls' | 'ready' | 'unavailable' | 'empty' = 'nulls';
let workMode: 'truncated' | 'full' | 'empty' = 'truncated';
// While set, every demo-metrics response is held until released: lets a test
// observe the loading state deterministically instead of racing a timer.
let demoGate: Promise<void> | null = null;

function metricsPayload() {
  return {
    mode: 'bounded_current',
    synthetic: true,
    session: {
      id: SESSION_ID,
      dataset_key: 'equa-demo-metrics-v1',
      session_key: SESSION_KEY,
      status: 'active',
      anchor_at: null,
      expires_at: null
    },
    generated_at: '2026-09-26T12:00:00Z',
    filters: { location_id: 12, include_descendants: false },
    cohort_size: 2,
    machines: [
      {
        alias: 'pump-1',
        machine_id: 5,
        coverage_state: 'fresh',
        condition: 'normal',
        required_signals: ['temp', 'vib'],
        present_signals: ['temp', 'vib'],
        fresh_signals: ['temp', 'vib'],
        last_observed_at: '2026-09-26T11:00:00Z'
      },
      {
        alias: 'pump-2',
        machine_id: 6,
        coverage_state: 'stale',
        condition: null,
        required_signals: ['temp'],
        present_signals: [],
        fresh_signals: [],
        last_observed_at: null
      }
    ],
    observation_coverage: {
      fresh: 1,
      partial: 0,
      stale: 1,
      never_seen: 0,
      not_configured: 0
    },
    fresh_condition: { normal: 1, warning: 0, critical: 0 },
    open_work_orders: 2,
    machines_with_open_work: 1,
    overdue_open_work_orders: 1,
    open_by_state: { in_progress: 1, planned: 1 },
    capabilities: {
      history_available: true,
      replay_available: false,
      anomaly_creation: false,
      oee: false
    }
  };
}

function workRow(id: number, machineId: number, open: boolean) {
  return {
    id,
    reference: `WO-${id}`,
    title: `Job ${id} on pump ${machineId}`,
    machine_id: machineId,
    priority: 'medium',
    lifecycle_status: open ? 'in_progress' : 'completed',
    is_active: open,
    open,
    due_date: open ? '2026-09-20' : '2026-09-10',
    overdue: open
  };
}

function workPayload() {
  // 'truncated': the server bounded the rows (count is the full total).
  // 'full': the complete session list (still only session-owned orders).
  // 'empty': an authorized but empty scope.
  const results =
    workMode === 'empty' ? [] : [workRow(101, 5, true), workRow(102, 6, false)];
  return {
    count: workMode === 'empty' ? 0 : workMode === 'truncated' ? 7 : 2,
    open_count: workMode === 'empty' ? 0 : workMode === 'truncated' ? 4 : 1,
    results_returned: results.length,
    has_more: workMode === 'truncated',
    filters: { location_id: 12, include_descendants: false },
    session: {
      id: SESSION_ID,
      session_key: SESSION_KEY,
      synthetic: true
    },
    results
  };
}

function historyPayload() {
  if (historyMode === 'unavailable') {
    return {
      available: false,
      synthetic: true,
      reason: 'no_imported_history_for_session',
      filters: { location_id: 12, include_descendants: false }
    };
  }
  const daily =
    historyMode === 'empty'
      ? []
      : [
          {
            date: '2026-09-24',
            planned_machine_minutes: 960,
            downtime_machine_minutes: 0,
            contributing_interval_ids: []
          },
          {
            date: '2026-09-25',
            planned_machine_minutes: 480,
            downtime_machine_minutes: null,
            contributing_interval_ids: []
          }
        ];
  return {
    available: true,
    synthetic: true,
    attribution_mode: 'synthetic_scenario',
    session_key: SESSION_KEY,
    filters: { location_id: 12, include_descendants: false },
    window: {
      start: '2026-09-12T00:00:00.000Z',
      end: '2026-09-26T00:00:00.000Z',
      timezone: 'UTC'
    },
    selected_machines: 2,
    measured_machines: 1,
    completeness: 'partial',
    daily,
    planned_machine_minutes: 1440,
    downtime_machine_minutes: historyMode === 'ready' ? 10 : null,
    measured_cohort_availability: historyMode === 'ready' ? 0.92 : null
  };
}

const boardWorkOrders = [
  {
    id: 101,
    priority: 'medium',
    due_date: '2026-09-30',
    machine: 5,
    machine_name: 'Pump 1',
    tags: [],
    company: '',
    company_contact_name: '',
    company_contact_phone: '',
    job_number: '',
    service_quote: '',
    parts: []
  },
  {
    id: 102,
    priority: 'high',
    due_date: '2026-09-30',
    machine: 6,
    machine_name: 'Pump 2',
    tags: [],
    company: '',
    company_contact_name: '',
    company_contact_phone: '',
    job_number: '',
    service_quote: '',
    parts: []
  },
  {
    id: 103,
    priority: 'low',
    due_date: '2026-10-05',
    machine: 6,
    machine_name: 'Pump 2',
    tags: [],
    company: '',
    company_contact_name: '',
    company_contact_phone: '',
    job_number: '',
    service_quote: '',
    parts: []
  }
];

const boardCards = boardWorkOrders.map((workOrder) => ({
  id: workOrder.id * 10,
  work_order: workOrder.id,
  card_kind: 'work_order',
  title: `Card for job ${workOrder.id}`,
  description: '',
  status: 'backlog',
  priority: workOrder.priority,
  assignee: '',
  machine: workOrder.machine,
  machine_name: workOrder.machine_name,
  tags: [],
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-20T10:00:00Z'
}));

const locatedMachine = {
  pk: 5,
  name: 'Pump 1',
  serial: 'S-5',
  active: true,
  manufacturer: 'EQUA',
  model: 'Demo',
  location: '',
  description: 'Demo machine',
  physical_location: {
    pk: 12,
    client: 1,
    parent: null,
    name: 'Line A',
    code: 'A',
    kind: 'area',
    description: '',
    timezone: 'UTC',
    effective_timezone: 'UTC',
    archived: false,
    version: 1,
    path: [{ pk: 12, name: 'Line A' }],
    has_children: false
  },
  placement_version: 1
};

async function installMocks(page: Page) {
  const requests: URL[] = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const path = url.pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, json: body as any });

    if (demoGate && path.includes('/demo-metrics/')) {
      await demoGate;
    }
    if (failMode !== 'none' && path.includes('/demo-metrics/')) {
      return json(
        { error: failMode === 'denied' ? 'SCOPE_DENIED' : 'SERVER_ERROR' },
        failMode === 'denied' ? 403 : 500
      );
    }
    if (path.endsWith('/demo-metrics/sessions/')) {
      return json({
        count: 1,
        results: [
          {
            id: SESSION_ID,
            dataset_key: 'equa-demo-metrics-v1',
            session_key: SESSION_KEY,
            mode: 'bounded_current',
            status: 'active',
            synthetic: true,
            anchor_at: null,
            expires_at: null
          }
        ]
      });
    }
    if (path.includes('/demo-metrics/') && path.endsWith('/metrics/')) {
      return json(metricsPayload());
    }
    if (path.includes('/demo-metrics/') && path.endsWith('/work-orders/')) {
      return json(workPayload());
    }
    if (path.includes('/demo-metrics/') && path.endsWith('/history/')) {
      return json(historyPayload());
    }
    if (path.includes('/kanban/work-orders/')) {
      return json(boardWorkOrders);
    }
    if (path.includes('/kanban/cards/')) {
      return json(boardCards);
    }
    if (path.includes('/kanban/columns/')) {
      return json([]);
    }
    const machineMatch = path.match(/\/api\/assets\/machines\/(\d+)\/$/);
    if (machineMatch) {
      return json({
        ...locatedMachine,
        pk: Number(machineMatch[1]),
        name: `Pump ${machineMatch[1]}`,
        description: ''
      });
    }
    if (path.endsWith('/assets/machines/')) {
      // The machine list is a bare array (used by the board and the
      // work-order create modal).
      return json([locatedMachine]);
    }
    if (path.endsWith('/locations/context/')) {
      return json({
        workspaces: [{ pk: 1, name: 'Demo client' }],
        can_add: false,
        can_change: false
      });
    }
    if (path.endsWith('/locations/machines/')) {
      return json({ count: 1, results: [locatedMachine], next: null });
    }
    return json({});
  });
  return requests;
}

function demoCache(page: Page) {
  return page.evaluate(
    () => (window as any).__demoTest.demoCacheWithData() as number
  );
}

test.beforeEach(() => {
  failMode = 'none';
  historyMode = 'nulls';
  workMode = 'truncated';
  demoGate = null;
});

test('explicit synthetic mode: live data is the default', async ({ page }) => {
  const requests = await installMocks(page);
  await page.goto('/playwright/demo-metrics.html?mode=panel&location=12');
  await expect(
    page.getByPlaceholder('Live data (no synthetic demo)')
  ).toBeVisible();
  await expect(page.getByText('Synthetic demo', { exact: true })).toHaveCount(
    0
  );
  await expect(
    page.getByText(/Historical performance will appear/)
  ).toBeVisible();
  // Without an explicit session no metric/work/history request fires.
  expect(requests.some((url) => url.pathname.includes('/metrics/'))).toBe(
    false
  );
});

test('current metrics render with session, location and descendant filters', async ({
  page
}) => {
  const requests = await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByText('Synthetic demo', { exact: true })).toBeVisible();
  await expect(page.getByText('bounded_current')).toBeVisible();
  await expect(
    page.getByText('Fresh (all signals)', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Stale (no fresh signal)', { exact: true })
  ).toBeVisible();
  await expect(page.getByText('Session machines')).toBeVisible();
  await expect(
    page.getByText('Open work orders', { exact: true })
  ).toBeVisible();
  await expect(page.getByText('Overdue open work orders')).toBeVisible();
  await expect(page.getByText('Normal', { exact: true })).toBeVisible();
  // The cohort filters reach the API, not just the query string.
  const metricsRequest = requests.find((url) =>
    url.pathname.endsWith('/metrics/')
  );
  expect(metricsRequest?.searchParams.get('location')).toBe('12');
  expect(metricsRequest?.searchParams.get('include_descendants')).toBe('false');
});

test('history renders with honest null coverage', async ({ page }) => {
  historyMode = 'nulls';
  const requests = await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByText('Scoped history')).toBeVisible();
  await expect(
    page.getByText('Measured 1 of 2 machines', { exact: true })
  ).toBeVisible();
  // Null downtime/availability are em dashes, never a zero.
  await expect(page.getByText('Downtime: —', { exact: true })).toBeVisible();
  await expect(
    page.getByText('Measured-cohort availability: —', { exact: true })
  ).toBeVisible();
  await expect(page.getByText('Synthetic scenario attribution')).toBeVisible();
  await expect(
    page.getByText(/Days without observation coverage are gaps/)
  ).toBeVisible();
  const historyRequest = requests.find((url) =>
    url.pathname.endsWith('/history/')
  );
  expect(historyRequest?.searchParams.get('start')).toBeTruthy();
  expect(historyRequest?.searchParams.get('end')).toBeTruthy();
});

test('measured history shows numeric values', async ({ page }) => {
  historyMode = 'ready';
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(
    page.getByText('Measured-cohort availability: 92.0%', { exact: true })
  ).toBeVisible();
  await expect(page.getByText('Downtime: 10', { exact: true })).toBeVisible();
});

test('history unavailable stays honestly unavailable', async ({ page }) => {
  historyMode = 'unavailable';
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByText('History unavailable')).toBeVisible();
  await expect(page.getByText(/no imported historical coverage/)).toBeVisible();
});

test('empty history window states no coverage', async ({ page }) => {
  historyMode = 'empty';
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(
    page.getByText(/No planned coverage falls inside the selected window/)
  ).toBeVisible();
});

test('loading states render while requests are in flight', async ({ page }) => {
  let release!: () => void;
  demoGate = new Promise((resolve) => {
    release = resolve;
  });
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  // The response is held, so the loading state is guaranteed on screen.
  await expect(page.locator('.mantine-Loader-root').first()).toBeVisible();
  release();
  await expect(page.getByText('Session machines')).toBeVisible();
});

test('a failed request is an error, never a fixture fallback', async ({
  page
}) => {
  failMode = 'error';
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByText('Demo metrics unavailable')).toBeVisible();
  await expect(
    page.getByText('No fixture fallback is shown.').first()
  ).toBeVisible();
  // No metric figures are invented.
  await expect(page.getByText('Session machines')).toHaveCount(0);
});

test('denied access clears every cached demo payload', async ({ page }) => {
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByText('Session machines')).toBeVisible();
  expect(await demoCache(page)).toBeGreaterThan(0);

  failMode = 'denied';
  await page.evaluate(() => (window as any).__demoTest.invalidateDemo());
  await expect(
    page.getByText(/does not cover this demo session/).first()
  ).toBeVisible();
  await expect.poll(async () => demoCache(page), { timeout: 5000 }).toBe(0);
});

test('sign-out clears every cached demo payload', async ({ page }) => {
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByText('Session machines')).toBeVisible();
  expect(await demoCache(page)).toBeGreaterThan(0);

  await page.evaluate(() => (window as any).__demoTest.signOut());
  await expect.poll(async () => demoCache(page), { timeout: 5000 }).toBe(0);
});

test('truncated contributing lists are announced, counts stay totals', async ({
  page
}) => {
  workMode = 'truncated';
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByText('Contributing list truncated')).toBeVisible();
  await expect(
    page.getByText('Showing the first 2 of 7 contributing rows.', {
      exact: false
    })
  ).toBeVisible();
  // The counts are the full totals, not the displayed rows.
  await expect(page.getByText('4 open of 7', { exact: true })).toBeVisible();
});

test('an empty contributing list is explicit', async ({ page }) => {
  workMode = 'empty';
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(
    page.getByText('No contributing session work for the current filters.')
  ).toBeVisible();
});

test('contributing-list machine links land on a filtered machine page', async ({
  page
}) => {
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await page.getByRole('link', { name: '#5' }).click();
  // The destination URL keeps the filters (the detail page redirects to its
  // default panel segment)…
  await expect(page.getByTestId('destination')).toHaveText(
    new RegExp(
      `^/machines/machine/5/[^?]*\\?demo_session=${SESSION_ID}&location=12&scope=direct$`
    )
  );
  // …and the destination applies them: the session scope notice reports the
  // machine's cohort-filtered coverage/condition from the metrics endpoint.
  await expect(
    page.getByText("In this session's filtered cohort:")
  ).toBeVisible();
  await expect(page.getByText(SESSION_KEY)).toBeVisible();
  await expect(page.getByText('Fresh (all signals)')).toBeVisible();
});

test('View in Maintenance lands on the filtered board', async ({ page }) => {
  const requests = await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await page.getByRole('link', { name: 'View in Maintenance' }).click();
  await expect(page.getByTestId('destination')).toHaveText(
    `/maintenance/board/?demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  // The board applies the filters: only session-owned jobs stay on it.
  await expect(page.getByText('Synthetic demo session filter')).toBeVisible();
  await expect(page.getByText('Card for job 101')).toBeVisible();
  await expect(page.getByText('Card for job 103')).toHaveCount(0);
  const boardScopeRequest = [...requests]
    .reverse()
    .find((url) =>
      url.pathname.endsWith(`/demo-metrics/sessions/${SESSION_ID}/work-orders/`)
    );
  expect(boardScopeRequest?.searchParams.get('location')).toBe('12');
  expect(boardScopeRequest?.searchParams.get('include_descendants')).toBe(
    'false'
  );
});

test('the maintenance board applies the demo scope on direct entry', async ({
  page
}) => {
  workMode = 'full';
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=board&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByText('Synthetic demo session filter')).toBeVisible();
  await expect(page.getByText(SESSION_KEY)).toBeVisible();
  await expect(
    page.getByText('location 12 · direct machines only')
  ).toBeVisible();
  // Session-owned jobs 101 and 102 stay; non-session job 103 is filtered out.
  await expect(page.getByText('Card for job 101')).toBeVisible();
  await expect(page.getByText('Card for job 102')).toBeVisible();
  await expect(page.getByText('Card for job 103')).toHaveCount(0);
});

test('without the session filter the board is unchanged', async ({ page }) => {
  await installMocks(page);
  await page.goto('/playwright/demo-metrics.html?mode=board');
  await expect(page.getByText('Synthetic demo session filter')).toHaveCount(0);
  await expect(page.getByText('Card for job 101')).toBeVisible();
  await expect(page.getByText('Card for job 103')).toBeVisible();
});

test('machine detail applies the session filters', async ({ page }) => {
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=machine&machine=5&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(
    page.getByText("In this session's filtered cohort:")
  ).toBeVisible();
  await expect(page.getByText(SESSION_KEY)).toBeVisible();
  // Coverage and condition render inside the cohort line (not exact-match).
  await expect(page.getByText('Fresh (all signals)')).toBeVisible();
  // The breadcrumb hands the same filters to the workspace that applies them
  // (the nav component prefixes the app base path).
  const breadcrumb = page.getByRole('link', { name: 'Machines' });
  await expect(breadcrumb).toHaveAttribute(
    'href',
    `/web/machines/index/?demo_session=${SESSION_ID}&location=12&scope=direct`
  );
});

test('a machine outside the filtered cohort says so', async ({ page }) => {
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=machine&machine=99&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(
    page.getByText(
      'This machine is not in the synthetic demo session cohort for the current filters.'
    )
  ).toBeVisible();
});

test('the panel lays out without horizontal overflow', async ({ page }) => {
  await installMocks(page);
  await page.goto(
    `/playwright/demo-metrics.html?mode=panel&demo_session=${SESSION_ID}&location=12&scope=direct`
  );
  await expect(page.getByText('Session machines')).toBeVisible();
  await expect(page.getByText('Contributing session work')).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
});
