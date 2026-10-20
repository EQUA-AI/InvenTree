import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import {
  mockFaultHistory,
  occurrenceDetail,
  occurrences
} from '../s3/machineWorkFixtures';
import { SIGNALS, mockPerformance } from '../s3/performanceFixtures';
import {
  mockEquipment,
  openEquipment,
  openExplorer,
  openFaultHistory
} from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();

test('Machines separates loaded-page counts, stale signals and unavailable operating facts', async ({
  page
}) => {
  await mockExplorer(page);
  await openExplorer(page, '/machines/index/sites/?location=12', 'dark');
  const summary = page.getByRole('region', { name: 'Loaded page summary' });
  await expect(summary).toContainText('25 / 80');
  await expect(summary).toContainText(
    '10 loaded machines report stale signals'
  );
  await expect(summary).toContainText('communication status are unavailable');
  await expect(page.getByText('Plant timezone: Unavailable')).toBeVisible();
  await expect(page.getByTestId('machine-count-view')).toHaveText('80');
  await expect(
    page.locator('td[data-condition="offline"]').first()
  ).toContainText('No current data');
  await expect(page.locator('td[data-condition="unavailable"]')).toHaveText(
    'Not available'
  );
  await expect(summary).not.toContainText('Running');
});

test('an empty station has zero recorded machines without a healthy or connected verdict', async ({
  page
}) => {
  const fixture = await mockExplorer(page);
  fixture.state.totalLimit = 0;
  fixture.location.counts.direct_machines = 0;
  fixture.location.counts.total_machines = 0;
  fixture.location.counts.direct_open_work_orders = 0;
  fixture.location.counts.total_open_work_orders = 0;
  await openExplorer(page, '/machines/index/sites/?location=12');
  await expect(
    page.getByText('No machines at this location or its sublocations.')
  ).toBeVisible();
  const summary = page.getByRole('region', { name: 'Loaded page summary' });
  await expect(summary).toContainText('0 / 0');
  await expect(summary).toContainText('communication status are unavailable');
  await expect(summary).not.toContainText('healthy');
});

test('failed machine refresh hides previous summary and retains a retry', async ({
  page
}) => {
  const fixture = await mockExplorer(page);
  await openExplorer(page, '/machines/index/machines/');
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toBeVisible();
  fixture.state.status = 503;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Machines unavailable' })
  ).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toHaveCount(0);
  fixture.state.status = 200;
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toBeVisible();
});

for (const state of ['running', 'stale', 'unknown']) {
  test(`schematic ${state} state never implies measured flow or communication`, async ({
    page
  }) => {
    await mockEquipment(page, state);
    await openEquipment(page, true, 'dark');
    await expect(
      page.getByRole('img', { name: 'Pump unit schematic' })
    ).toBeVisible();
    await expect(page.getByText('Flow direction: unavailable')).toBeVisible();
    await expect(
      page.getByText('Communication status: unavailable')
    ).toBeVisible();
    await expect(
      page.getByText('Reference schematic · physical arrangement unverified')
    ).toBeVisible();
    if (state === 'stale')
      await expect(page.getByText('7.5 MW', { exact: true })).toHaveCount(0);
    await expect(
      page.getByText('Open · warning', { exact: true })
    ).toBeVisible();
    await expect(
      page.getByText('Acknowledged · warning', { exact: true })
    ).toBeVisible();
    await expect(page.getByText('Dismissed', { exact: true })).toBeVisible();
    const unknown = page.locator('tr[data-anomaly="904"]');
    await expect(unknown).toContainText('Alarm state unavailable');
    await expect(
      unknown.getByRole('button', { name: 'Acknowledge' })
    ).toHaveCount(0);
    await expect(
      unknown.getByRole('button', { name: 'Dismiss', exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByText(
        'Acknowledgement records that someone saw the application alarm; it does not clear the condition.'
      )
    ).toBeVisible();
  });
}

test('polling failure is labelled as a polling error and hides the old readings', async ({
  page
}) => {
  const data = await mockEquipment(page);
  data.last_error_code = 'connection_failed';
  await openEquipment(page, true);
  await expect(
    page.getByText('Last station polling error: connection_failed')
  ).toBeVisible();
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({ status: 503, json: {} })
  );
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(
    page.getByText(
      'Live readings are unavailable. Refresh to retry; cached values are hidden.'
    )
  ).toBeVisible();
  await expect(
    page.getByRole('img', { name: 'Pump unit schematic' })
  ).toHaveCount(0);
});

test('model reference label leads and keyboard selection keeps exact component records', async ({
  page
}) => {
  await mockEquipment(page);
  await openEquipment(page, false, 'dark');
  await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible();
  await expect(
    page.getByText('Reference model · geometry unverified')
  ).toBeVisible();
  const bearing = page.locator('[data-position="bearing"]');
  await bearing.focus();
  await bearing.press('Enter');
  await expect(page.locator('[data-selected-position]')).toContainText(
    'Motor drive-end bearing'
  );
  await expect(
    page.getByText('DE bearing temperature', { exact: true })
  ).toBeVisible();
  await page.locator('[data-position="impeller"]').click();
  await expect(
    page.getByText('DE bearing temperature', { exact: true })
  ).toHaveCount(0);
});

test('Fault History distinguishes active, acknowledged and resolved without asserting cleared', async ({
  page
}) => {
  await mockFaultHistory(page);
  await openFaultHistory(page, { scheme: 'dark' });
  await expect(page.locator('[data-occurrence="902"]').first()).toContainText(
    'Active'
  );
  await expect(page.locator('[data-occurrence="900"]').first()).toContainText(
    'Acknowledged, not resolved'
  );
  await expect(page.locator('[data-occurrence="901"]').first()).toContainText(
    'Reading back inside its limits.'
  );
  await expect(
    page.getByText(
      'Resolved does not always mean cleared: read the recorded resolution reason. Acknowledged conditions remain active.'
    )
  ).toBeVisible();
});

test('event trend adds a labelled bounded context and preserves the canonical occurrence', async ({
  page
}, info) => {
  const requests = await mockPerformance(page, {
    signals: SIGNALS.slice(0, 3)
  });
  await mockFaultHistory(page);
  await openFaultHistory(page, { query: '?occurrence=901' });
  const link = page.getByRole('link', {
    name: 'Trend around first observation · 30 min before and after'
  });
  await expect(link).toBeVisible();
  const url = new URL(
    (await link.getAttribute('href'))!,
    'https://example.invalid'
  );
  expect(url.pathname).toBe('/machines/machine/18/performance/');
  expect(url.searchParams.get('occurrence')).toBe('901');
  expect(url.searchParams.get('history_clock')).toBe('recorded');
  expect(url.searchParams.get('history_from')).toBe('2026-10-12T01:44:05.000Z');
  expect(url.searchParams.get('history_to')).toBe('2026-10-12T02:44:05.000Z');
  const before = requests.length;
  await link.click();
  await expect(page.getByText('Anything unusual?')).toBeVisible({
    timeout: 30_000
  });
  await expect.poll(() => requests.length).toBe(before + 1);
  expect(requests.at(-1)!.searchParams.get('from')).toBe(
    '2026-10-12T01:44:05.000Z'
  );
  expect(requests.at(-1)!.searchParams.get('to')).toBe(
    '2026-10-12T02:44:05.000Z'
  );
  await expect(page.getByTestId('linked-evidence')).toContainText(
    'Retained occurrence 901'
  );
  await page.screenshot({
    path: info.outputPath('event-context-trend.png'),
    fullPage: false
  });
});

test('resolved without a clearing reading retains its full reason and does not assert cleared', async ({
  page
}, info) => {
  await mockFaultHistory(page);
  const reason =
    'Closed without a clearing reading: this binding no longer carries a threshold, or it stopped reporting. The condition was not observed to end.';
  await page.route('**/fault-history/occurrences/?*', (route) =>
    route.fulfill({
      json: {
        rule: 'fault_occurrences.v1',
        machine: 18,
        count: 1,
        limit: 25,
        offset: 0,
        results: [{ ...occurrences[1], resolution_note: reason }]
      }
    })
  );
  await page.route('**/fault-history/occurrences/901/', (route) =>
    route.fulfill({
      json: { ...occurrenceDetail(901), resolution_note: reason }
    })
  );
  await openFaultHistory(page, { scheme: 'dark' });
  const row = page.locator('[data-occurrence="901"]').first();
  await expect(row).toContainText('Resolved');
  await expect(row).toContainText(reason);
  await expect(row.getByText('Cleared', { exact: true })).toHaveCount(0);
  const reasonText = row.getByText(reason, { exact: true });
  expect(
    await reasonText.evaluate((el) => el.scrollHeight <= el.clientHeight + 1)
  ).toBe(true);
  for (const badge of await row.locator('.mantine-Badge-label').all()) {
    expect(
      await badge.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
      'Severity and outcome labels must stay whole beside a long reason'
    ).toBe(true);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1
    )
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath('resolved-unassessable-dark.png'),
    fullPage: true
  });
});

test('a mapped numeric zero discharge flow is shown with its source unit without implying direction', async ({
  page
}) => {
  const data = await mockEquipment(page, 'unknown');
  data.layout.elements.push({
    id: 'pump-flow',
    pointer: '/pd/{pump}/dv',
    view: 'unit',
    role: 'value',
    label: 'Discharge flow',
    x: 606,
    y: 518
  });
  data.points['/pd/P1/dv'] = {
    ...data.points['/dex/PUMP1_POWER'],
    pointer: '/pd/P1/dv',
    label: 'Discharge flow',
    value: 0,
    unit: 'm^3/s',
    quality: 'good',
    reason: null
  };
  await openEquipment(page, true);
  await expect(
    page
      .getByLabel('Process data availability')
      .getByText('Discharge flow: 0 m^3/s', { exact: true })
  ).toBeVisible();
  await expect(page.getByText('Flow direction: unavailable')).toBeVisible();
});
