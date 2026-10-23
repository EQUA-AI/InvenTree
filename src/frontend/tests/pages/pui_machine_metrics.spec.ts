/** Mocked browser regressions against production API contracts; no authenticated backend integration. */
import { type Page, expect, test } from '@playwright/test';
import type {
  MachineAnomaly,
  MachineHealthSummary,
  MachineSignal,
  SignalTrend
} from '../../lib/types/MachineHealth';
let healthFailure: 'signals' | 'anomalies' | null = null;
let summaryFailure = false;
let summaryGate: Promise<void> | null = null;
let showHealthAnomaly = false;
let showHealthSignal = false;
let failHealth = false;
let failWork = false;
let failHealthTemporary = false;
let healthNotFound = false;
let workForbidden = false;
let excludedMachine: number | undefined;
let unconfigured = false;
let populationGate: Promise<void> | null = null;
let boardAllocationFacts = false;
const boardWorkOrders = [
  {
    id: 101,
    reference: 'WO-101',
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
    reference: 'WO-102',
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
    reference: 'WO-103',
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
  model: 'Pump',
  location: '',
  description: 'Production machine',
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

const independentAnomaly = {
  pk: 81,
  machine: 5,
  source: 1,
  source_name: 'Plant historian',
  source_type: 'historian',
  external_id: 'bearing-warning',
  alarm_code: 'BEARING_TEMP',
  fingerprint: 'bearing-warning-81',
  severity: 'warning',
  status: 'open',
  title: 'Bearing temperature warning',
  evidence_summary: 'Recorded bearing temperature warning',
  metrics: {},
  detector: 'source_alarm',
  detector_version: '1',
  signals: [
    {
      binding_id: 7,
      display_name: 'Bearing temperature',
      unit: '°C',
      signal_kind: 'temperature'
    }
  ],
  first_observed_at: '2026-09-26T09:00:00Z',
  last_observed_at: '2026-09-26T09:10:00Z',
  acknowledged_at: null,
  acknowledged_by_name: null,
  acknowledgement_note: '',
  resolved_at: null,
  resolution_note: '',
  work_order: null,
  repair_packet: null,
  created_at: '2026-09-26T09:00:00Z',
  updated_at: '2026-09-26T09:10:00Z'
} satisfies MachineAnomaly;

async function installMocks(page: Page) {
  const requests: URL[] = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const path = url.pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, json: body as any });
    if (path.endsWith('/maintenance-metrics/')) {
      // Keep the selected machine's contributor read successful so only the
      // aggregate work denial can establish the access boundary.
      if (workForbidden && url.searchParams.get('machine') === '6')
        return json({ detail: 'Scope denied' }, 403);
      if (failWork) return json({ detail: 'Temporarily unavailable' }, 503);
      const metric = url.searchParams.get('metric')!;
      const machine = Number(url.searchParams.get('machine'));
      const offset = Number(url.searchParams.get('offset') ?? 0);
      const value = machine === 5 ? (metric === 'open' ? 28 : 3) : 0;
      const records = Array.from({ length: value }, (_, i) => ({
        pk: 101 + i,
        model: 'workorder',
        label: `WO-${101 + i}`,
        machine_label: `Pump ${machine}`,
        visible_machine: machine,
        lifecycle_status: 'planned',
        due_date: '2026-10-21'
      }));
      return json({
        id: metric,
        version: 1,
        state: 'ready',
        complete: true,
        value,
        unit: 'work_orders',
        record_count: value,
        detail_count: value,
        records: records.slice(offset, offset + 25),
        next_offset: offset + 25 < value ? offset + 25 : null,
        groups: [],
        missing: {},
        stats: {},
        observed_at: '2026-10-22T05:01:00Z',
        timezone: 'America/Chicago',
        reporting_date: '2026-10-22',
        from: '2026-10-01T05:00:00Z',
        to: '2026-11-01T05:00:00Z',
        clock: 'now'
      });
    }
    const overview = path.match(/kanban\/work-orders\/(\d+)\/overview\//);
    if (overview) {
      const id = Number(overview[1]);
      return json({
        ...boardWorkOrders.find((order) => order.id === id),
        id,
        reference: `WO-${id}`,
        title: `Full work order ${id}`,
        description: 'Complete work-order details',
        status: 'backlog',
        lifecycle_status: 'planned',
        work_order_type: 'corrective',
        parts: [],
        cards: [],
        dependencies: [],
        events: [],
        canonical_commands_enabled: false
      });
    }
    if (path.includes('/machine-health/')) {
      if (path.endsWith('/health/')) {
        if (summaryGate) await summaryGate;
        if (summaryFailure)
          return json({ detail: 'Summary temporarily unavailable' }, 503);
      }
      if (path.endsWith('/snapshots/')) return json({ count: 0, results: [] });
      if (
        showHealthAnomaly &&
        path.endsWith('/anomalies/') &&
        url.searchParams.get('status') === 'resolved'
      )
        return json({
          count: 1,
          results: [
            {
              ...independentAnomaly,
              pk: 82,
              title: 'Resolved bearing event',
              status: 'resolved',
              resolved_at: '2026-09-26T09:10:00Z'
            }
          ]
        });
      if (healthFailure && path.endsWith(`/${healthFailure}/`))
        return json({ detail: 'Service unavailable' }, 503);
      if (path.endsWith('/trend/') || path.endsWith('/trends/')) {
        const trend: SignalTrend = {
          available: true,
          binding_id: 7,
          signal_kind: 'temperature',
          source_id: 1,
          source_type: 'historian',
          max_samples: 4320,
          limits: { max_window_seconds: 21600, max_samples: 4320 },
          display_name: 'Bearing temperature',
          unit: '°C',
          source_name: 'Plant historian',
          window_start: '2026-09-26T09:00:00Z',
          window_end: '2026-09-26T09:10:00Z',
          samples: [
            { observed_at: '2026-09-26T09:10:00Z', value: 20, quality: 'good' },
            {
              observed_at: '2026-09-26T09:09:00Z',
              value: 100,
              quality: 'uncertain'
            },
            {
              observed_at: '2026-09-26T09:01:00Z',
              value: null,
              quality: 'good'
            },
            { observed_at: '2026-09-26T09:00:00Z', value: 10, quality: 'good' }
          ]
        };
        return json(path.endsWith('/trends/') ? { results: [trend] } : trend);
      }
      if (showHealthSignal && path.endsWith('/signals/'))
        return json({
          results: [
            {
              binding_id: 7,
              source_id: 1,
              external_key: '/dex/PUMP1_BEARING_TEMP',
              received_at: '2026-09-26T09:10:00Z',
              freshness_threshold_seconds: 300,
              display_name: 'Bearing temperature',
              signal_kind: 'temperature',
              value: 20,
              unit: '°C',
              state: 'normal',
              observed_at: '2026-09-26T09:10:00Z',
              quality: 'good',
              stale: false,
              source_name: 'Plant historian',
              source_type: 'historian',
              limits: {
                normal_min: null,
                normal_max: null,
                warn_min: null,
                warn_max: null,
                critical_min: null,
                critical_max: null
              }
            } satisfies MachineSignal
          ]
        });
      if (showHealthAnomaly && path.endsWith('/anomalies/'))
        return json({ count: 1, results: [independentAnomaly] });
      if (path.endsWith('/signals/') || path.endsWith('/anomalies/'))
        return json({ count: 0, results: [] });
      const pk = Number(path.match(/machines\/(\d+)/)?.[1] ?? 5);
      if (pk === 6 && healthNotFound)
        return json({ detail: 'Not found.' }, 404);
      if (pk === 6 && failHealthTemporary)
        return json({ detail: 'Temporarily unavailable' }, 503);
      if (pk === 6 && failHealth) return json({ detail: 'Unavailable' }, 403);
      return json({
        configured: !unconfigured,
        state: unconfigured ? 'unknown' : pk === 5 ? 'critical' : 'offline',
        signal_count: 1,
        stale_signal_count: 1,
        degraded_data: false,
        last_observed_at: '2026-10-21T10:00:00Z',
        anomaly_counts: { critical: 0, warning: 0, info: 0 },
        active_anomaly_count: showHealthAnomaly ? 1 : 0,
        sources: []
      } satisfies MachineHealthSummary);
    }
    if (path.includes('/kanban/work-orders/')) {
      return json(
        boardWorkOrders.map((order) =>
          boardAllocationFacts && order.id === 101
            ? {
                ...order,
                parts: [
                  {
                    part: 41,
                    part_name: 'Seal kit',
                    quantity: 7,
                    allocation_note: 'Allocation read still pending'
                  },
                  {
                    part: 42,
                    part_name: 'Bearing kit',
                    quantity: 4,
                    allocated_quantity: 2,
                    allocation_status: 'partial',
                    allocation_note: '2 staged from stock'
                  }
                ]
              }
            : order
        )
      );
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
        workspaces: [{ pk: 1, name: 'Plant client' }],
        can_add: false,
        can_change: false
      });
    }
    if (path.endsWith('/locations/machines/')) {
      if (populationGate && url.searchParams.get('location') === '12')
        await populationGate;
      const direct = url.searchParams.get('include_descendants') === 'false';
      const ids = (
        url.searchParams.get('location') === '99' ? [9] : direct ? [5] : [5, 6]
      ).filter((id) => id !== excludedMachine);
      const offset = Number(url.searchParams.get('offset') ?? 0);
      return json({
        count: ids.length,
        next: offset + 1 < ids.length ? 'next' : null,
        results: ids
          .slice(offset, offset + 1)
          .map((pk) => ({ ...locatedMachine, pk, name: `Pump ${pk}` }))
      });
    }
    return json({});
  });
  return requests;
}
test.beforeEach(() => {
  healthFailure = null;
  summaryFailure = false;
  summaryGate = null;
  showHealthAnomaly = false;
  showHealthSignal = false;
  failHealth = false;
  failWork = false;
  failHealthTemporary = false;
  healthNotFound = false;
  workForbidden = false;
  excludedMachine = undefined;
  unconfigured = false;
  populationGate = null;
  boardAllocationFacts = false;
});
test('collapsed cards open the parent work order and retain density and filters', async ({
  page
}) => {
  await installMocks(page);
  await page.goto(
    '/playwright/machine-metrics.html?mode=board&location=12&scope=direct'
  );
  await page.getByRole('textbox', { name: 'Search', exact: true }).fill('101');
  await page.getByText('Collapsed cards', { exact: true }).click();
  const card = page.getByTestId('maintenance-card').first();
  await expect(card).toHaveAttribute('data-density', 'collapsed');
  await expect(card.getByLabel('Status')).toHaveCount(0);
  const link = card.getByRole('link', {
    name: 'Open work order: Card for job 101'
  });
  await expect(link).toHaveAttribute(
    'href',
    '/maintenance/work-orders/101/?location=12&scope=direct'
  );
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(
    page.getByRole('heading', { name: 'WO-101: Full work order 101' })
  ).toBeVisible();
  await page.getByRole('button', { name: 'Back to work list' }).click();
  await expect(page.getByTestId('maintenance-card').first()).toHaveAttribute(
    'data-density',
    'collapsed'
  );
  await expect(
    page.getByRole('textbox', { name: 'Search', exact: true })
  ).toHaveValue('101');
  await page.reload();
  await expect(page.getByTestId('maintenance-card').first()).toHaveAttribute(
    'data-density',
    'collapsed'
  );
  await expect(
    page.getByRole('textbox', { name: 'Search', exact: true })
  ).toHaveValue('101');
});

test('collapsed card body navigates and its action menu does not', async ({
  page
}) => {
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?mode=board');
  await page.getByText('Collapsed cards', { exact: true }).click();
  const card = page.getByTestId('maintenance-card').first();
  await card.getByRole('button', { name: /Actions for/ }).click();
  await expect(
    page.getByRole('menuitem', { name: 'Edit work order' })
  ).toBeVisible();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/board/'
  );
  await page.keyboard.press('Escape');
  await expect(
    page.getByRole('menuitem', { name: 'Edit work order' })
  ).toBeHidden();
  await card.click({ position: { x: 30, y: 80 } });
  await expect(
    page.getByRole('heading', { name: 'WO-101: Full work order 101' })
  ).toBeVisible();
});

test('expanded cards and secondary filters remain available', async ({
  page
}) => {
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?mode=board');
  await page.getByText('Collapsed cards', { exact: true }).click();
  await page.getByText('Expanded cards', { exact: true }).click();
  await expect(
    page.getByTestId('maintenance-card').first().getByLabel('Status')
  ).toBeVisible();
  await page.getByRole('button', { name: 'More filters' }).click();
  await expect(
    page.getByRole('combobox', { name: 'Column', exact: true })
  ).toBeVisible();
});

test('blank assignee remains available as a reasoned dash in both card densities', async ({
  page
}) => {
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?mode=board');
  const card = page.getByTestId('maintenance-card').first();
  for (const density of ['Expanded', 'Collapsed'] as const) {
    await page.getByText(`${density} cards`, { exact: true }).click();
    await expect(card).toHaveAttribute('data-density', density.toLowerCase());
    const assignee = card.getByRole('button', {
      name: 'Assignee: Unassigned',
      exact: true
    });
    await expect(assignee).toHaveText('—');
    await assignee.hover();
    await expect(page.getByRole('tooltip')).toHaveText('Assignee: Unassigned');
    await page.mouse.move(0, 0);
    await expect(page.getByRole('tooltip')).toBeHidden();
    await assignee.focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    await expect(assignee).toBeFocused();
    await expect(page.getByRole('tooltip')).toHaveText('Assignee: Unassigned');
    await page.keyboard.press('Tab');
  }
});

test('missing allocation retains the required quantity and supplied note', async ({
  page
}, info) => {
  boardAllocationFacts = true;
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?mode=board');
  await page.getByText('Expanded cards', { exact: true }).click();
  const card = page.getByTestId('maintenance-card').first();
  const missingAllocation = card.getByRole('button', {
    name: 'Allocated quantity not supplied. Allocation read still pending',
    exact: true
  });
  const allocationTooltip = page.getByRole('tooltip', {
    name: 'Allocated quantity not supplied. Allocation read still pending',
    exact: true
  });
  await expect(missingAllocation).toHaveText('—');
  await expect(missingAllocation.locator('..')).toHaveText(/—\s*\/7/);
  await expect(card.getByText('0/7', { exact: true })).toHaveCount(0);
  await missingAllocation.hover();
  await expect(allocationTooltip).toHaveText(
    'Allocated quantity not supplied. Allocation read still pending'
  );
  await page.mouse.move(0, 0);
  await expect(allocationTooltip).toBeHidden();
  await missingAllocation.focus();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Shift+Tab');
  await expect(missingAllocation).toBeFocused();
  await expect(allocationTooltip).toHaveText(
    'Allocated quantity not supplied. Allocation read still pending'
  );
  await page.keyboard.press('Tab');
  const allocated = card.getByRole('button', { name: '2/4', exact: true });
  await expect(allocated).toBeVisible();
  await allocated.focus();
  await expect(
    page.getByRole('tooltip', { name: '2 staged from stock', exact: true })
  ).toHaveText('2 staged from stock');
  await page.getByRole('textbox', { name: 'Search', exact: true }).focus();
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await card.screenshot({
    path: info.outputPath('allocation-facts-card.png'),
    animations: 'disabled'
  });
});

for (const section of ['signals', 'anomalies'] as const) {
  test(`failed ${section} request shows an independent error and recovers`, async ({
    page
  }) => {
    healthFailure = section;
    await installMocks(page);
    await page.goto('/playwright/machine-metrics.html?mode=health');
    await expect(
      page.getByText(
        section === 'signals'
          ? 'Signal readings unavailable'
          : 'Active anomalies unavailable',
        { exact: true }
      )
    ).toBeVisible();
    await expect(
      page.getByText(
        section === 'signals'
          ? 'No signals are mapped for this machine.'
          : '0 active anomalies',
        { exact: true }
      )
    ).toHaveCount(0);
    await expect(
      page.getByRole('heading', { name: 'Current condition' })
    ).toBeVisible();
    healthFailure = null;
    await page.getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(
      page.getByText(
        section === 'signals'
          ? 'No signals are mapped for this machine.'
          : '0 active anomalies',
        { exact: true }
      )
    ).toBeVisible();
  });
}

test('dragging a collapsed card does not accidentally navigate', async ({
  page
}) => {
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?mode=board');
  await page.getByText('Collapsed cards', { exact: true }).click();
  const card = page.getByTestId('maintenance-card').first();
  // Dispatch one browser gesture atomically; protocol latency must not consume the click guard.
  await card.evaluate((element) => {
    const dataTransfer = new DataTransfer();
    element.dispatchEvent(
      new DragEvent('dragstart', { bubbles: true, dataTransfer })
    );
    element.dispatchEvent(
      new DragEvent('dragend', { bubbles: true, dataTransfer })
    );
    element
      .querySelector('a')!
      .dispatchEvent(
        new MouseEvent('click', { bubbles: true, cancelable: true, detail: 1 })
      );
  });
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/board/'
  );
});

test('rendered signal trends preserve quality gaps and expose their units and source', async ({
  page
}) => {
  showHealthSignal = true;
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?mode=health');
  if ((page.viewportSize()?.width ?? 1280) < 768) {
    const details = page.getByRole('button', {
      name: 'Details for Bearing temperature',
      exact: true
    });
    await details.focus();
    await page.keyboard.press('Enter');
    await expect(details).toHaveAttribute('aria-expanded', 'true');
  }
  const chart = page.getByRole('img', {
    name: /Recent trend for Bearing temperature:/
  });
  await expect(chart).toBeVisible();
  await expect(chart).toHaveAttribute('aria-label', /10–20 °C/);
  await expect(chart).toHaveAttribute('aria-label', /Plant historian/);
  await expect(chart).toHaveAttribute(
    'aria-label',
    /2 excluded readings; 1 uncertain/
  );
  const path = await chart.locator('path').getAttribute('d');
  expect(path?.match(/M/g)).toHaveLength(2);
  expect(path).not.toContain('L');
  // Dense rows keep this quality evidence in the accessible chart name and tooltip.
  await expect(chart).toHaveAttribute(
    'aria-label',
    /2 excluded readings; 1 uncertain, 0 bad/
  );
});

test('all placement pages contribute to authoritative counts without session reads', async ({
  page
}) => {
  const requests = await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Overdue open work orders 3' })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Open work orders: 28' })
  ).toBeVisible();
  await expect(page.getByText('Machines with open work: 1')).toBeVisible();
  await expect(page.getByText(/2026-10-22 · America\/Chicago/)).toBeVisible();
  expect(
    requests
      .filter((u) => u.pathname.endsWith('/locations/machines/'))
      .map((u) => u.searchParams.get('offset'))
  ).toEqual(['0', '1']);
  expect(requests.some((u) => /history|sessions/.test(u.pathname))).toBe(false);
  expect(
    requests
      .filter((u) => u.pathname.endsWith('/maintenance-metrics/'))
      .every((u) => !u.searchParams.has('location'))
  ).toBe(true);
});

test('history is visible as unavailable with ineffective controls disabled', async ({
  page
}) => {
  const requests = await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByText('Historical data unavailable', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('combobox', { name: 'History period' })
  ).toBeDisabled();
  await expect(
    page.getByRole('button', {
      name: /^(Downtime|Observed planned time|Measured availability).*Historical data unavailable/
    })
  ).toHaveCount(3);
  expect(requests.some((u) => u.pathname.includes('/history/'))).toBe(false);
});

test('contributor pages do not change authoritative totals', async ({
  page
}) => {
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12&scope=direct');
  await expect(
    page.getByRole('link', { name: 'WO-101', exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Showing 1–25 of 28', { exact: true })
  ).toBeVisible();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(
    page.getByRole('link', { name: 'WO-126', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Open work orders: 28' })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Next', exact: true })
  ).toBeDisabled();
  await page.getByRole('button', { name: 'Previous', exact: true }).click();
  await expect(
    page.getByRole('link', { name: 'WO-101', exact: true })
  ).toHaveAttribute(
    'href',
    '/maintenance/work-orders/101/?location=12&scope=direct'
  );
});

test('direct location scope filters board cards', async ({ page }) => {
  await installMocks(page);
  await page.goto(
    '/playwright/machine-metrics.html?mode=board&location=12&scope=direct'
  );
  await expect(page.getByTestId('maintenance-card')).toHaveCount(1);
  await expect(page.getByTestId('maintenance-card')).toContainText('101');
});

test('one denied health member hides incomplete counts and cached payloads', async ({
  page
}) => {
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
  failHealth = true;
  await page.evaluate(() => (window as any).__metricsTest.invalidateMetrics());
  await expect(
    page.getByText('Metrics access denied', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).__metricsTest.cacheWithData())
    )
    .toBe(0);
});

test('sign-out clears authorized payloads', async ({ page }) => {
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
  await page.evaluate(() => (window as any).__metricsTest.signOut());
  await expect(page.getByText('Signed out', { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => (window as any).__metricsTest.cacheWithData())
  ).toBe(0);
});

test('unconfigured monitoring is explicit', async ({ page }) => {
  unconfigured = true;
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByText('Monitoring not configured: 2', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Condition unavailable 2' })
  ).toBeVisible();
});

test('work failure leaves health usable without partial work totals', async ({
  page
}) => {
  failWork = true;
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
  await expect(page.getByTestId('read-error-notice').first()).toBeVisible({
    timeout: 15000
  });
  await expect(
    page.getByRole('button', {
      name: 'Overdue open work orders: Open work could not be checked'
    })
  ).toBeVisible();
});

test('desktop and narrow layouts have no horizontal overflow', async ({
  page
}, info) => {
  await page.setViewportSize({
    width: info.project.name === 'desktop' ? 1440 : 320,
    height: 900
  });
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth
    )
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath('machine-metrics.png'),
    fullPage: true
  });
});

test('scope changes ignore obsolete in-flight population reads', async ({
  page
}) => {
  let release!: () => void;
  populationGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requests = await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect
    .poll(() =>
      requests.some((r) => r.pathname.endsWith('/locations/machines/'))
    )
    .toBe(true);
  await page.evaluate(() => (window as any).__metricsTest.setScope(99));
  await expect(
    page.getByRole('button', { name: 'Critical machines 0' })
  ).toBeVisible();
  release();
  await expect(
    page.getByRole('button', { name: 'Critical machines 0' })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toHaveCount(0);
  expect(
    requests
      .filter((r) => r.pathname.includes('/machine-health/'))
      .every((r) => r.pathname.includes('/machines/9/'))
  ).toBe(true);
});

test('explicit recovery after denial refetches without a cache-removal loop', async ({
  page
}) => {
  failHealth = true;
  const requests = await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByText('Metrics access denied', { exact: true })
  ).toBeVisible();
  const initial = requests.length;
  await page.waitForTimeout(200);
  expect(requests.length).toBe(initial);
  failHealth = false;
  await page
    .getByTestId('read-error-notice')
    .getByRole('button', { name: 'Retry', exact: true })
    .last()
    .click();
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
});

test('temporary health failure cannot mask a simultaneous work access denial', async ({
  page
}) => {
  const requests = await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'WO-101', exact: true })
  ).toBeVisible();
  failHealthTemporary = true;
  await page.evaluate(() => (window as any).__metricsTest.invalidateMetrics());
  await expect
    .poll(() =>
      page.evaluate(() =>
        (window as any).__metricsTest
          .queries()
          .some(
            (query: any) =>
              query.key.includes('health') && query.status === 'error'
          )
      )
    )
    .toBe(true);
  workForbidden = true;
  await page.evaluate(() => (window as any).__metricsTest.invalidateMetrics());
  await expect(
    page.getByText('Metrics access denied', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'WO-101', exact: true })
  ).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).__metricsTest.cacheWithData())
    )
    .toBe(0);
  const stopped = requests.length;
  await page.evaluate(() => (window as any).__metricsTest.invalidateMetrics());
  await page.waitForTimeout(200);
  expect(requests.length).toBe(stopped);
  failHealthTemporary = false;
  workForbidden = false;
  await page
    .getByTestId('read-error-notice')
    .getByRole('button', { name: 'Retry', exact: true })
    .last()
    .click();
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
});

test('revoked object health scope returns 404 and clears all earlier metrics', async ({
  page
}) => {
  const requests = await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
  await expect(
    page.getByRole('link', { name: 'WO-101', exact: true })
  ).toBeVisible();
  healthNotFound = true;
  await page.evaluate(() => (window as any).__metricsTest.invalidateMetrics());
  await expect(
    page.getByText('Metrics access unavailable', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Open work orders: 28' })
  ).toHaveCount(0);
  await expect(
    page.getByRole('combobox', { name: 'Contributing machine' })
  ).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).__metricsTest.cacheWithData())
    )
    .toBe(0);
  const stopped = requests.length;
  await page.evaluate(() => (window as any).__metricsTest.invalidateMetrics());
  await page.waitForTimeout(200);
  expect(requests.length).toBe(stopped);
  healthNotFound = false;
  await page
    .getByTestId('read-error-notice')
    .getByRole('button', { name: 'Retry', exact: true })
    .last()
    .click();
  await expect(
    page.getByRole('button', { name: 'Critical machines 1' })
  ).toBeVisible();
});

test('population refresh removes a departed contributor machine and resets its page', async ({
  page
}) => {
  const requests = await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByRole('combobox', { name: 'Contributing machine' })
  ).toHaveValue('Pump 5');
  // Exercise a saved explicit selection, not just the default first member.
  const machine = page.getByRole('combobox', { name: 'Contributing machine' });
  for (const name of ['Pump 6', 'Pump 5']) {
    await machine.click();
    await page.getByRole('option', { name, exact: true }).click();
    await expect(machine).toHaveValue(name);
  }
  await expect(
    page.getByRole('link', { name: 'WO-101', exact: true })
  ).toBeVisible();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(
    page.getByRole('link', { name: 'WO-126', exact: true })
  ).toBeVisible();
  excludedMachine = 5;
  await page.evaluate(() => (window as any).__metricsTest.invalidateMetrics());
  await expect(
    page.getByRole('combobox', { name: 'Contributing machine' })
  ).toHaveValue('Pump 6');
  await expect(
    page.getByRole('button', { name: 'Critical machines 0' })
  ).toBeVisible();
  await expect(
    page.getByText('Showing 0–0 of 0', { exact: true })
  ).toBeVisible();
  await expect(page.getByRole('link', { name: /^WO-/ })).toHaveCount(0);
  const settled = requests.length;
  await page.evaluate(() => (window as any).__metricsTest.invalidateMetrics());
  expect(
    requests
      .slice(settled)
      .filter((url) => url.pathname.endsWith('/maintenance-metrics/'))
      .every(
        (url) =>
          url.searchParams.get('machine') === '6' &&
          url.searchParams.get('offset') === '0'
      )
  ).toBe(true);
});

test('contributor columns stay reachable by keyboard on a narrow phone', async ({
  page
}) => {
  await page.setViewportSize({ width: 320, height: 844 });
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?location=12');
  await expect(
    page.getByRole('link', { name: 'WO-101', exact: true })
  ).toBeVisible();
  const viewport = page.getByRole('group', {
    name: 'Contributing work columns',
    exact: true
  });
  await expect(viewport).toHaveAttribute('tabindex', '0');
  await page.getByRole('combobox', { name: 'Contributing machine' }).focus();
  await page.keyboard.press('Tab');
  await expect(viewport).toBeFocused();
  for (let step = 0; step < 8; step++) await page.keyboard.press('ArrowRight');
  await expect
    .poll(() => viewport.evaluate((node) => node.scrollLeft))
    .toBeGreaterThan(0);
  await expect
    .poll(() =>
      page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
    )
    .toBe(true);
});

test('direct work entry keeps location scope through R8 tabs and board return', async ({
  page
}) => {
  await installMocks(page);
  await page.goto(
    '/playwright/machine-metrics.html?mode=work&location=12&scope=direct'
  );
  await expect(
    page.getByRole('heading', { name: 'WO-101: Full work order 101' })
  ).toBeVisible();
  await page.getByRole('tab', { name: 'Closeout', exact: true }).click();
  await expect(page.getByTestId('destination')).toContainText('location=12');
  await expect(page.getByTestId('destination')).toContainText('scope=direct');
  await page
    .getByRole('button', { name: 'Back to work list', exact: true })
    .click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/board/?location=12&scope=direct'
  );
  await expect(page.getByTestId('maintenance-card')).toHaveCount(1);
});

test('summary 503 keeps successful signals anomalies history and evidence available', async ({
  page
}, info) => {
  summaryFailure = true;
  showHealthSignal = true;
  showHealthAnomaly = true;
  await installMocks(page);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/playwright/machine-metrics.html?mode=health');
  await expect(
    page.getByText('Health data unavailable', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('textbox', { name: 'Search signals', exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Bearing temperature warning', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', {
      name: 'Acknowledge Bearing temperature warning',
      exact: true
    })
  ).toBeEnabled();
  await expect(
    page.getByRole('button', {
      name: 'Create repair for Bearing temperature warning',
      exact: true
    })
  ).toBeEnabled();
  await expect(
    page.getByText('Resolved bearing event', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Fault snapshots', exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('health-summary')).toHaveCount(0);
  await expect(
    page.locator('output').filter({ hasText: 'Condition now' })
  ).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Source connections', exact: true })
  ).toHaveCount(0);
  await page
    .getByRole('button', { name: 'Inspect selected signal', exact: true })
    .click();
  await expect(
    page.getByRole('heading', {
      name: 'Signal evidence: Bearing temperature',
      exact: true
    })
  ).toBeVisible();
  await page.screenshot({
    path: info.outputPath('summary-unavailable-independent-health.png'),
    fullPage: true,
    animations: 'disabled'
  });
  expect(errors).toEqual([]);
});

for (const section of ['signals', 'anomalies'] as const) {
  test(`summary and ${section} 503 retain separate retries and successful sections`, async ({
    page
  }) => {
    summaryFailure = true;
    healthFailure = section;
    showHealthSignal = true;
    showHealthAnomaly = true;
    const requests = await installMocks(page);
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto('/playwright/machine-metrics.html?mode=health');
    const summaryError = page
      .getByRole('alert')
      .filter({ hasText: 'Health data unavailable' });
    const sectionError = page.getByRole('alert').filter({
      hasText:
        section === 'signals'
          ? 'Signal readings unavailable'
          : 'Active anomalies unavailable'
    });
    await expect(summaryError).toBeVisible();
    await expect(sectionError).toBeVisible();
    await expect(
      page.getByText('Resolved bearing event', { exact: true })
    ).toBeVisible();
    if (section === 'signals')
      await expect(
        page.getByText('Bearing temperature warning', { exact: true })
      ).toBeVisible();
    else
      await expect(
        page.getByRole('textbox', { name: 'Search signals', exact: true })
      ).toBeVisible();
    const failedReads = () =>
      requests.filter(
        (url) =>
          url.pathname.endsWith(`/${section}/`) &&
          url.searchParams.get('status') !== 'resolved'
      ).length;
    const before = failedReads();
    summaryFailure = false;
    await summaryError
      .getByRole('button', { name: 'Retry', exact: true })
      .click();
    await expect(page.getByTestId('health-summary')).toBeVisible();
    await expect(sectionError).toBeVisible();
    expect(failedReads()).toBe(before);
    healthFailure = null;
    await sectionError
      .getByRole('button', { name: 'Retry', exact: true })
      .click();
    await expect(sectionError).toHaveCount(0);
    await expect(
      page.getByRole('textbox', { name: 'Search signals', exact: true })
    ).toBeVisible();
    await expect(
      page.getByText('Bearing temperature warning', { exact: true })
    ).toBeVisible();
    expect(failedReads()).toBe(before + 1);
    expect(errors).toEqual([]);
  });
}

test('failed summary refresh suppresses cached condition without removing other sections', async ({
  page
}) => {
  showHealthSignal = true;
  showHealthAnomaly = true;
  await installMocks(page);
  await page.goto('/playwright/machine-metrics.html?mode=health');
  await expect(page.getByTestId('health-summary')).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Source connections', exact: true })
  ).toBeVisible();
  summaryFailure = true;
  await page
    .getByRole('button', { name: 'Refresh', exact: true })
    .first()
    .click();
  const summaryError = page
    .getByRole('alert')
    .filter({ hasText: 'Health data unavailable' });
  await expect(summaryError).toBeVisible();
  await expect(page.getByTestId('health-summary')).toHaveCount(0);
  await expect(
    page.locator('output').filter({ hasText: 'Condition now' })
  ).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Source connections', exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByRole('textbox', { name: 'Search signals', exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Bearing temperature warning', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Resolved bearing event', { exact: true })
  ).toBeVisible();
  summaryFailure = false;
  await summaryError
    .getByRole('button', { name: 'Retry', exact: true })
    .click();
  await expect(page.getByTestId('health-summary')).toBeVisible();
});

test('pending summary keeps independent Health reads and history visible', async ({
  page
}) => {
  let release!: () => void;
  summaryGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  showHealthSignal = true;
  showHealthAnomaly = true;
  await installMocks(page);
  try {
    await page.goto('/playwright/machine-metrics.html?mode=health');
    await expect(
      page.getByLabel('Loading health data', { exact: true })
    ).toBeVisible();
    await expect(
      page.getByRole('textbox', { name: 'Search signals', exact: true })
    ).toBeVisible();
    await expect(
      page.getByText('Bearing temperature warning', { exact: true })
    ).toBeVisible();
    await expect(
      page.getByText('Resolved bearing event', { exact: true })
    ).toBeVisible();
    await expect(page.getByTestId('health-summary')).toHaveCount(0);
    release();
    await expect(page.getByTestId('health-summary')).toBeVisible();
  } finally {
    release();
  }
});

for (const screen of ['panel', 'board', 'machine'] as const) {
  for (const view of [
    { name: 'desktop-light', width: 1440, height: 900, theme: 'light' },
    { name: 'desktop-dark', width: 1440, height: 900, theme: 'dark' },
    { name: 'phone', width: 390, height: 844, theme: 'light' }
  ] as const) {
    test(`merge capture ${screen} ${view.name}`, async ({ page }, info) => {
      test.skip(
        info.project.name !== 'desktop',
        'Capture matrix specifies its own viewports.'
      );
      await page.setViewportSize({ width: view.width, height: view.height });
      await installMocks(page);
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.goto(
        `/playwright/machine-metrics.html?mode=${screen}&machine=5&location=12&theme=${view.theme}`
      );
      if (screen === 'panel')
        await expect(
          page.getByRole('button', { name: 'Critical machines 1' })
        ).toBeVisible();
      else if (screen === 'board')
        await expect(
          page.getByTestId('maintenance-card').first()
        ).toBeVisible();
      else
        await expect(
          page.getByRole('cell', { name: 'Pump 5', exact: true })
        ).toBeVisible();
      await page.keyboard.press('Escape');
      await page.mouse.move(0, 0);
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth
          )
        )
        .toBe(true);
      await page.screenshot({
        path: info.outputPath(`${screen}-${view.name}.png`),
        fullPage: true,
        animations: 'disabled'
      });
      expect(errors).toEqual([]);
    });
  }
}
