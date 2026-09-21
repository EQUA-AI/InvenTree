import { type Page, expect, test } from '@playwright/test';

function result(id: string, offset = 0) {
  return {
    id,
    version: 1,
    state: 'ready',
    complete: true,
    value: 28,
    unit: 'work_orders',
    record_count: 28,
    detail_count: 28,
    groups: [{ key: 'planned', label: 'planned', value: 28, count: 28 }],
    missing: { due_date: 2 },
    stats: {},
    records: [
      {
        pk: offset + 1,
        model: 'workorder',
        label: `WO-${offset + 1}`,
        due_date: '2026-09-20',
        machine_label: 'Pump',
        visible_machine: 1,
        lifecycle_status: 'planned'
      }
    ],
    next_offset: offset === 0 ? 25 : null,
    observed_at: '2026-09-21T12:00:00Z',
    timezone: 'UTC',
    from: '2026-09-01T00:00:00Z',
    to: '2026-09-21T12:00:00Z',
    partial_period: true,
    clock: 'now',
    machines: [{ value: '1', label: 'Pump' }]
  };
}
async function mockMetrics(page: Page) {
  const requests: URL[] = [];
  await page.route('**/api/aichat/ui/maintenance-metrics/**', (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    return route.fulfill({
      json: result(
        url.searchParams.get('metric')!,
        Number(url.searchParams.get('offset') || 0)
      )
    });
  });
  return requests;
}

test('all fifteen tiles render, with no creation/completion comparison', async ({
  page
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const requests = await mockMetrics(page);
  await page.goto('/playwright/maintenance-widgets.html');
  await expect(
    page.getByRole('button', { name: /View contributing records/ })
  ).toHaveCount(15);
  expect(new Set(requests.map((r) => r.searchParams.get('metric'))).size).toBe(
    15
  );
  await expect(page.getByText('Created vs completed')).toHaveCount(0);
  expect(errors).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
});

test('detail pagination and record navigation preserve the metric definition', async ({
  page
}) => {
  const requests = await mockMetrics(page);
  await page.goto('/playwright/maintenance-widgets.html?metric=open');
  await page.getByRole('button', { name: /View contributing records/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(
    dialog.getByRole('link', { name: 'WO-1', exact: true })
  ).toBeVisible();
  await dialog.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(
    dialog.getByRole('link', { name: 'WO-26', exact: true })
  ).toBeVisible();
  expect(requests.at(-1)?.searchParams.get('offset')).toBe('25');
  await dialog.getByRole('link', { name: 'WO-26', exact: true }).click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/work-orders/26/'
  );
  await expect(dialog).toBeHidden();
});

test('period changes and filters persist across reload', async ({ page }) => {
  const requests = await mockMetrics(page);
  await page.goto('/playwright/maintenance-widgets.html?metric=completed');
  await page.getByRole('combobox', { name: 'Reporting period' }).click();
  await page.getByRole('option', { name: 'Last 30 days' }).click();
  await expect
    .poll(() => requests.at(-1)?.searchParams.get('period'))
    .toBe('30');
  await page.getByRole('button', { name: 'Filters', exact: true }).click();
  await page.getByRole('checkbox', { name: 'Assigned to me' }).check();
  await expect
    .poll(() => requests.at(-1)?.searchParams.get('mine'))
    .toBe('true');
  await page.reload();
  await expect(
    page.getByRole('combobox', { name: 'Reporting period' })
  ).toHaveValue('Last 30 days');
  await expect
    .poll(() => requests.at(-1)?.searchParams.get('mine'))
    .toBe('true');
});

test('custom periods wait for both valid boundaries', async ({ page }) => {
  const requests = await mockMetrics(page);
  await page.goto('/playwright/maintenance-widgets.html?metric=completed');
  await page.getByRole('combobox', { name: 'Reporting period' }).click();
  await page.getByRole('option', { name: 'Custom', exact: true }).click();
  await expect(
    page.getByText('Choose a valid start and end date.')
  ).toBeVisible();
  await page.getByLabel('From', { exact: true }).fill('2026-08-01');
  await page.getByLabel('Through', { exact: true }).fill('2026-08-31');
  await expect
    .poll(() => requests.at(-1)?.searchParams.get('to'))
    .toBe('2026-08-31');
  expect(requests.at(-1)?.searchParams.get('from')).toBe('2026-08-01');
});

test('unknown scope and null measurements never display a false zero', async ({
  page
}) => {
  await page.route('**/api/aichat/ui/maintenance-metrics/**', (route) =>
    route.fulfill({
      json: { id: 'open', version: 1, state: 'unavailable', complete: false }
    })
  );
  await page.goto('/playwright/maintenance-widgets.html?metric=open');
  await expect(
    page.getByText('Unavailable for your current role or maintenance scope.')
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: /View contributing records/ })
  ).toHaveCount(0);
  await page.unroute('**/api/aichat/ui/maintenance-metrics/**');
  await page.route('**/api/aichat/ui/maintenance-metrics/**', (route) =>
    route.fulfill({
      json: {
        ...result('elapsed'),
        value: null,
        unit: 'minutes',
        record_count: 0
      }
    })
  );
  await page.goto('/playwright/maintenance-widgets.html?metric=elapsed');
  await expect(
    page.getByText('No qualifying records', { exact: true })
  ).toBeVisible();
});

test('permission revocation removes cached counts', async ({ page }) => {
  await mockMetrics(page);
  await page.goto('/playwright/maintenance-widgets.html?metric=open');
  await expect(page.getByText('28 work orders', { exact: true })).toBeVisible();
  await page.unroute('**/api/aichat/ui/maintenance-metrics/**');
  await page.route('**/api/aichat/ui/maintenance-metrics/**', (route) =>
    route.fulfill({ status: 403, json: {} })
  );
  await page.getByRole('button', { name: 'Refresh fixture' }).click();
  await expect(page.getByText('28 work orders', { exact: true })).toHaveCount(
    0
  );
});
