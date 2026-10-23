/** Mocked browser regressions against production API contracts; no authenticated backend integration. */
import { type Page, expect, test } from '@playwright/test';
let healthFailure: 'signals' | 'anomalies' | null = null;
let showHealthSignal = false;
let failHealth = false;
let failWork = false;
let unconfigured = false;
let populationGate: Promise<void> | null = null;
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

async function installMocks(page: Page) {
  const requests: URL[] = [];
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const path = url.pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, json: body as any });
    if (path.endsWith('/maintenance-metrics/')) {
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
      if (healthFailure && path.endsWith(`/${healthFailure}/`))
        return json({ detail: 'Service unavailable' }, 503);
      if (path.endsWith('/trend/'))
        return json({
          available: true,
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
        });
      if (showHealthSignal && path.endsWith('/signals/'))
        return json({
          results: [
            {
              binding_id: 7,
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
                warn_min: null,
                warn_max: null,
                critical_min: null,
                critical_max: null
              }
            }
          ]
        });
      if (path.endsWith('/signals/') || path.endsWith('/anomalies/'))
        return json({ count: 0, results: [] });
      const pk = Number(path.match(/machines\/(\d+)/)?.[1] ?? 5);
      if (pk === 6 && failHealth) return json({ detail: 'Unavailable' }, 403);
      return json({
        configured: !unconfigured,
        state: unconfigured ? 'unknown' : pk === 5 ? 'critical' : 'offline',
        signal_count: 1,
        stale_signal_count: 1,
        degraded_data: false,
        last_observed_at: '2026-10-21T10:00:00Z',
        anomaly_counts: { critical: 0, warning: 0, info: 0 },
        sources: []
      });
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
        workspaces: [{ pk: 1, name: 'Plant client' }],
        can_add: false,
        can_change: false
      });
    }
    if (path.endsWith('/locations/machines/')) {
      if (populationGate && url.searchParams.get('location') === '12')
        await populationGate;
      const direct = url.searchParams.get('include_descendants') === 'false';
      const ids =
        url.searchParams.get('location') === '99' ? [9] : direct ? [5] : [5, 6];
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
  showHealthSignal = false;
  failHealth = false;
  failWork = false;
  unconfigured = false;
  populationGate = null;
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
  await page.getByRole('button', { name: 'work-order-back' }).click();
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

for (const section of ['signals', 'anomalies'] as const) {
  test(`failed ${section} request shows an independent error and recovers`, async ({
    page
  }) => {
    healthFailure = section;
    await installMocks(page);
    await page.goto('/playwright/machine-metrics.html?mode=health');
    await expect(
      page.getByText(
        section === 'signals' ? 'Signals unavailable' : 'Anomalies unavailable',
        { exact: true }
      )
    ).toBeVisible();
    await expect(
      page.getByText(
        section === 'signals'
          ? 'No signals are mapped for this machine.'
          : 'No active anomalies.',
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
          : 'No active anomalies.',
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
  await expect(
    page.getByText('2 missing or poor-quality readings', { exact: true })
  ).toBeVisible();
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
  await expect(page.getByLabel('Unavailable', { exact: true })).toHaveCount(3);
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
  await expect(page.getByText(/Rows shown: 25/)).toBeVisible();
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
    page.getByText('Earlier metrics are hidden because access was denied.')
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
    page.getByText('Monitoring not configured', { exact: true })
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
    page.getByRole('button', { name: 'Overdue open work orders —' })
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
    page.getByText('Earlier metrics are hidden because access was denied.')
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
