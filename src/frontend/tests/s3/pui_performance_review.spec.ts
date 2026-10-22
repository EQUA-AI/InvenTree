import { type Page, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { openPerformance, signal } from './performanceFixtures';
import { BAYS, openStation } from './stationFixtures';
nativeBaseline();
const card = (page: Page, title: string) =>
  page
    .locator('.mantine-Paper-root')
    .filter({ has: page.getByText(title, { exact: true }) })
    .first();
const winding = (
  i: number,
  unit: string,
  state: 'normal' | 'critical' = 'normal'
) =>
  signal(
    `/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED${i}`,
    i === 4 ? 190 : 80 + i,
    unit,
    `Winding ${i}`,
    { state, limits: { warn_max: 125, critical_max: 145 } }
  );
test('P11-1 an uncompared critical sensor keeps the family tile critical', async ({
  page
}, info) => {
  await openPerformance(page, {
    harness: 's3',
    signals: [
      { ...winding(1, 'degC'), limits: {} },
      { ...winding(2, 'degC'), limits: {} },
      { ...winding(3, 'degC'), limits: {} },
      winding(4, 'degF', 'critical')
    ]
  });
  const tile = page.locator('[data-kpi="winding"]');
  await expect(tile.getByText('Critical', { exact: true })).toBeVisible();
  await tile.getByText('Winding temperature', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText('Winding 4 (°F) critical, not compared');
  await expect(page.getByRole('tooltip')).toContainText('Highest of 3');
  await expect(tile).not.toContainText('no limit configured');
  await tile.screenshot({
    path: info.outputPath('mixed-unit-critical-kpi.png'),
    animations: 'disabled'
  });
});
test('P11-2 absent mapping differs from returned unavailable history', async ({
  page
}) => {
  await openStation(page, {
    absentKeys: ['/dex/PUMP3_ACTIVE_POWER'],
    bays: BAYS.map((b) => (b.key === 'P2' ? { ...b, noHistory: true } : b))
  });
  const power = card(page, 'Active power by bay');
  await expect(power).toContainText('No power channel mapped for Pump 3.');
  await expect(power).toContainText('No history in this window for Pump 2.');
  await expect(power).toContainText(
    'The source could not be reached for this window.'
  );
  await expect(power).not.toContainText('No history in this window for Pump 3');
});
test('P11-2 loading does not assert empty history', async ({ page }) => {
  await openStation(page, { delaySeriesMs: 5000 });
  await expect(page.getByText(/No history in this window for/)).toHaveCount(0);
});
test('P11-2 and P11-8 failed station request has no empty-window assertion or green Live', async ({
  page
}) => {
  await openStation(page, { seriesStatus: 500 });
  await expect(
    page.getByText('Could not read history for this window', { exact: true })
  ).toBeVisible();
  await expect(page.getByText(/No history in this window for/)).toHaveCount(0);
  await expect(page.getByText('Read failed', { exact: true })).toBeVisible();
  await expect(
    page.locator('.mantine-Badge-root').filter({ hasText: /^Live$/ })
  ).toHaveCount(0);
});
test('P11-3 denied bay list is explicit and station-only read continues', async ({
  page
}) => {
  const requests = await openStation(page, { mimicStatus: 403 });
  const alert = page
    .getByRole('alert')
    .filter({ hasText: 'Bay list unavailable' });
  await expect(alert).toContainText('The bay list could not be read');
  await expect(
    alert.getByRole('button', { name: 'Retry bay list' })
  ).toBeVisible();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].searchParams.get('keys')?.split(',')).toHaveLength(4);
  await expect(page.locator('tbody')).toHaveCount(0);
});
test('P11-4 unconfirmed unit is explicit under a stated column', async ({
  page
}) => {
  await openStation(page, {
    bays: BAYS.map((b) => (b.key === 'P3' ? { ...b, powerUnit: '' } : b))
  });
  await expect(page.locator('thead th').nth(2)).toHaveText('Active power (MW)');
  await expect(
    page.locator('tbody tr').filter({ hasText: 'Pump 3' }).locator('td').nth(2)
  ).toContainText('(no unit)');
});
test('P11-4 unconfirmed units count toward column majority', async ({
  page
}) => {
  await openStation(page, {
    bays: BAYS.map((b) => (b.key !== 'P3' ? { ...b, powerUnit: '' } : b))
  });
  await expect(page.locator('thead th').nth(2)).toHaveText('Active power');
  await expect(
    page.locator('tbody tr').filter({ hasText: 'Pump 3' }).locator('td').nth(2)
  ).toContainText('MW');
});
for (const scheme of ['light', 'dark'] as const)
  test(`P11-5 mimic fallback distinguishes response time from observation time in ${scheme}`, async ({
    page
  }, info) => {
    await openStation(page, {
      scheme,
      bays: BAYS.map((b) => (b.key === 'P2' ? { ...b, status: null } : b))
    });
    const row = page.locator('tbody tr').filter({ hasText: 'Pump 2' });
    await expect(row).toContainText(
      /Station mimic read at .*18 Oct.*12:00.*not an observation time.*not from this window/
    );
    await expect(row).not.toContainText('Current, not from this window');
    await row.screenshot({
      path: info.outputPath(`mimic-read-time-${scheme}.png`)
    });
  });
test('P11-6 split families keep caller caveats and explain heatmap threshold', async ({
  page
}) => {
  await openPerformance(page, {
    harness: 's3',
    signals: [
      winding(1, 'degC'),
      winding(2, 'degC'),
      winding(3, 'degC'),
      winding(4, 'degF')
    ]
  });
  const section = page.locator('[data-section="Motor winding temperature"]');
  await expect(section.getByText(/Every winding RTD on one axis/)).toHaveCount(0);
  await expect(section.getByText('Stator winding temperature (°C)', { exact: true })).toBeVisible();
  await expect(section.getByText('Stator winding temperature (°F)', { exact: true })).toBeVisible();
  await expect(
    section.getByText(
      /Heatmap not drawn: this unit group has fewer than four sensors/
    )
  ).toHaveCount(2);
});
test('P11-7 unconfirmed delta pairs retain separate inlet and outlet axes', async ({
  page
}) => {
  await openPerformance(page, {
    harness: 's3',
    signals: [
      signal('/dex/PUMP1_PUMP_COOLING_WATER_INLET_TEMP1', 29, 'degC', 'In 1'),
      signal('/dex/PUMP1_PUMP_COOLING_WATER_OUTLET_TEMP1', 34, 'degC', 'Out 1'),
      signal('/dex/PUMP1_PUMP_COOLING_WATER_INLET_TEMP2', 84, 'degF', 'In 2'),
      signal('/dex/PUMP1_PUMP_COOLING_WATER_OUTLET_TEMP2', 95, 'degF', 'Out 2')
    ]
  });
  const section = page.locator('[data-section="Cooling and thermal"]');
  await expect(section.locator('[data-difference-unit]')).toHaveCount(0);
  await expect(section).toContainText('Cooling water');
  await expect(section.locator('[data-axis-title]')).toContainText(['°C', '°F', '°C', '°F']);
});
test('P11-8 failed pump read cannot show green Live', async ({ page }) => {
  await openPerformance(page, { harness: 's3', failSeries: () => true });
  await expect(page.getByText('Read failed', { exact: true })).toBeVisible();
  await expect(
    page.locator('.mantine-Badge-root').filter({ hasText: /^Live$/ })
  ).toHaveCount(0);
});
for (const scheme of ['light', 'dark'] as const)
  test(`P11-9 window announcements contain state changes and keep moving clock text silent in ${scheme}`, async ({
    page
  }, info) => {
    await openPerformance(page, {
      harness: 's3',
      scheme,
      signals: [
        signal('/dex/PUMP1_ACTIVE_POWER', 8.42, 'MW', 'Active power'),
        signal('/dex/PUMP1_SPEED', 745, 'rpm', 'Shaft speed')
      ],
      delaySeriesMs: (_url, index) => (index ? 3000 : 0)
    });
    const region = page.locator('[data-window-status] [aria-live="polite"]');
    await expect(region).toBeEmpty();
    await expect(page.getByText('Window on screen:', { exact: true })).toHaveCount(0);
    await expect(page.getByTestId('live-indicator')).toHaveAttribute('aria-live', 'off');
    const element = await region.elementHandle();
    await page.getByText('6 h', { exact: true }).click();
    await expect(region).toHaveText('Updating selected history window.');
    await expect(page.locator('[data-window-status]')).toContainText('Updating');
    await expect(page.getByText(/Charts still show the previous window/)).toHaveCount(0);
    expect(await element?.evaluate((el) => el.isConnected)).toBe(true);
    await page.locator('[data-window-status]').screenshot({
      path: info.outputPath(`window-announcement-${scheme}.png`),
      animations: 'disabled'
    });
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    await expect(region).toBeEmpty();
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    await expect(region).toBeEmpty();
  });
