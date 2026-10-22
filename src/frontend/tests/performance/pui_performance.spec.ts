import { type Locator, expect, test } from '@playwright/test';

import { MACHINE, SIGNALS, mount } from './fixtures';

void MACHINE;

async function expectFactTooltip(target: Locator, name: string | RegExp) {
  await expect(target).toHaveAttribute('aria-describedby', /\S+/);
  const id = await target.getAttribute('aria-describedby');
  expect(id).toBeTruthy();
  // Use the target association to keep lookup local on the large heatmap page.
  const tooltip = target.page().locator(`[id="${id}"]`);
  await expect(tooltip).toHaveAttribute('role', 'tooltip');
  await expect(tooltip).toHaveAccessibleName(name);
  await expect(tooltip).toBeVisible();
}

let pageErrors: string[] = [];
test.beforeEach(({ page }) => {
  pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
});
test.afterEach(() => {
  expect(
    pageErrors,
    'The component harness must not hide runtime errors'
  ).toEqual([]);
});

test('the KPI strip shows fixture values and states what it cannot show', async ({
  page
}) => {
  await mount(page);
  const power = page.locator('[data-kpi="power"]');
  await expect(power).toContainText('8.42');
  await expect(power).toContainText('MW');
  await expect(page.locator('[data-kpi="speed"]')).toContainText('745');
  await expect(
    page.locator('[data-kpi="vibration"]').getByRole('button', {
      name: /no vibration channel with a confirmed role and unit/i
    })
  ).toHaveText('—');
  // Winding 4 is pegged at the over-range marker with quality bad: it is not
  // the highest sensor, and 3276.7 appears nowhere on the tile.
  const winding = page.locator('[data-kpi="winding"]');
  const windingLabel = winding.locator('p').first().locator(':scope > span');
  await expect(windingLabel).toHaveCount(1);
  await expect(windingLabel).toContainText('Highest of 10: Winding 11');
  await windingLabel.hover();
  await expectFactTooltip(windingLabel, /^Highest of 10:/);
  await expect(winding).not.toContainText('3,276.7');
  await expect(winding).toContainText('82');
  await expect(power.locator('time')).toContainText('20 s ago');
  await expect(power.locator('time')).not.toContainText('just now');
  await expect(page.locator('[data-kpi="status"]')).toContainText('Running');
  // The one sensor without a reading is reported as poor quality, so the
  // strip is not calm - and must not claim to be.
  await expect(
    page.getByRole('button', { name: /^2 with poor quality:/ })
  ).toBeVisible();
  await expect(page.getByText('No exceptions', { exact: true })).toHaveCount(0);
  await page.keyboard.press('Tab');
  const coverage = page.getByRole('button', {
    name: 'Exceptions: assessment coverage',
    exact: true
  });
  await coverage.focus();
  await expect(coverage).toBeFocused();
  await expectFactTooltip(coverage, /11 have configured limits/);
  await expect(coverage).toHaveAccessibleDescription(
    /11 have configured limits/
  );
});

test('families retain heatmaps and omit unconfirmed temperature rises', async ({
  page
}) => {
  await mount(page);
  await expect(
    page.getByText('Stator winding temperature', { exact: true })
  ).toBeVisible();
  await expect(page.getByText(/Highest now: Winding 11/)).toBeVisible();
  await expect(page.getByText(/Highest now: Winding 4/)).toHaveCount(0);
  await expect(page.getByText(/Spread .* \(calculated\)/)).toHaveCount(0);
  const winding = page.locator('[data-section="Motor winding temperature"]');
  await winding.scrollIntoViewIfNeeded();
  await expect(
    winding.getByRole('img', { name: 'Sensor heatmap', exact: true })
  ).toBeVisible({ timeout: 20_000 });
  await expect(
    page.getByText('Cooling water rise (outlet − inlet)')
  ).toHaveCount(0);
  await expect(
    page.getByText('Relationship: active power and shaft speed')
  ).toBeVisible();
  // Said twice on purpose: on the KPI tile and as the section's own state.
  await expect(page.locator('[data-kpi="vibration"]')).toContainText('—');
  await expect(
    page.getByText(
      'Unavailable · no vibration channel with a confirmed role and unit',
      { exact: true }
    )
  ).toHaveCount(1);
  // Absent families stay out of navigation; their mounted fallback must still explain why.
  await expect(
    page.locator('[data-section="Vibration and mechanical"]')
  ).toContainText(
    'Channels without a confirmed engineering role and unit cannot be used for vibration comparison.'
  );
  await expect(
    page.getByText(/Unclassified tags remain in Other signals/)
  ).toHaveCount(0);
  // Unusable current readings are named beside their quality; the tooltip
  // explains why they have no plotted point.
  const comparison = winding.getByTestId('sensor-comparison');
  const omitted = comparison
    .getByText('Winding 4', { exact: true })
    .locator('..');
  await expect(omitted).toBeVisible();
  const quality = omitted.getByRole('button', {
    name: 'Bad quality',
    exact: true
  });
  await page.keyboard.press('Tab');
  await quality.scrollIntoViewIfNeeded();
  await expect(quality).toBeInViewport();
  await quality.focus();
  await expect(quality).toBeFocused();
  await expectFactTooltip(quality, 'No usable current reading; not plotted');
  await expect(quality).toHaveAccessibleDescription(
    'No usable current reading; not plotted'
  );
  const plottedSensors = comparison
    .locator('svg text title')
    .filter({ hasText: /^Winding \d+$/ });
  await expect(plottedSensors).toHaveCount(10);
  await expect(plottedSensors).toHaveText(
    [11, 10, 9, 8, 7, 6, 5, 3, 2, 1].map((number) => `Winding ${number}`)
  );
  await expect(plottedSensors.filter({ hasText: /^Winding 4$/ })).toHaveCount(
    0
  );
  const healthyCells = winding.locator(
    '[data-heatmap-cell][data-limit-state="normal"]'
  );
  expect(await healthyCells.count()).toBeGreaterThan(0);
  const fills = await healthyCells.evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute('fill'))
  );
  expect(
    fills.every(
      (fill) => fill !== null && fill.trim() !== '' && fill.trim() !== 'none'
    )
  ).toBe(true);
  expect(new Set(fills).size).toBe(1);
});

test('the time range control changes what is asked of the server', async ({
  page
}) => {
  const requests = await mount(page);
  // An hour is 720 snapshots, past the complete-read bound, so the default
  // window is sampled; fifteen minutes is read whole.
  await page.getByText('Updated', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText(
    'Sampled: one reading every'
  );
  await page.mouse.move(0, 0);
  await page.getByText('15 min', { exact: true }).click();
  await page.getByText('Updated', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText(
    'Every reading in the window'
  );
  await page.mouse.move(0, 0);
  const before = requests.length;
  await page.getByText('6 h', { exact: true }).click();
  await expect.poll(() => requests.length).toBeGreaterThan(before);
  const last = requests[requests.length - 1];
  const span =
    Date.parse(last.searchParams.get('to')!) -
    Date.parse(last.searchParams.get('from')!);
  expect(Math.round(span / 3600_000)).toBe(6);
  await page.getByText('Updated', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText(
    'Sampled: one reading every'
  );
  await page.mouse.move(0, 0);
});

test('live can be paused and says so', async ({ page }) => {
  await mount(page);
  const live = page.getByRole('switch', {
    name: 'Live updates: Live',
    exact: true
  });
  await expect(live).toHaveCount(1);
  await expect(live).toBeChecked();
  await expect(page.getByText('Live', { exact: true })).toHaveCount(1);
  // Mantine's track label sits over the input; the click must go through it.
  await live.click({ force: true });
  const paused = page.getByRole('switch', {
    name: 'Live updates: Paused',
    exact: true
  });
  await expect(paused).not.toBeChecked();
  await expect(page.getByText('Paused', { exact: true })).toHaveCount(1);
});

test('a failed history read keeps the current values and says the charts are empty', async ({
  page
}) => {
  await mount(page, { fail: true });
  await expect(page.locator('[data-kpi="power"]')).toContainText('8.42');
  await expect(
    page.getByText('The source could not serve this window')
  ).toBeVisible();
  await expect(
    page.getByText(`${SIGNALS.length} without history in this window`)
  ).toBeVisible();
});

test('dragging across a chart offers a zoom, and taking it narrows the window', async ({
  page
}) => {
  const requests = await mount(page);
  await page.getByText('Updated', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText(
    'Sampled: one reading every'
  );
  await page.mouse.move(0, 0);
  const chart = page
    .locator('[data-section="Electrical"] .recharts-wrapper')
    .first();
  await expect(chart.locator('.recharts-line-curve').first()).toBeVisible();
  await expect(chart).toBeVisible();
  // Raw mouse events do not scroll: the chart must be inside the viewport.
  await chart.scrollIntoViewIfNeeded();
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()))
      )
  );
  await page.screenshot({ path: test.info().outputPath('drag-before.png') });
  const box = (await chart.boundingBox())!;
  // Drag across the middle third of the plot area.
  const y = box.y + box.height * 0.45;
  await page.mouse.move(box.x + box.width * 0.35, y);
  // Recharts computes its active axis value on the next animation frame.
  // Wait for that real hover state before pressing, rather than racing it.
  await expect(chart.locator('.recharts-tooltip-wrapper')).toBeVisible();
  await expect(chart.locator('.recharts-tooltip-wrapper')).toContainText(
    'Active power'
  );
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.45, y, { steps: 4 });
  await page.mouse.move(box.x + box.width * 0.6, y, { steps: 4 });
  await page.mouse.up();
  await page.screenshot({ path: test.info().outputPath('drag-after.png') });
  const zoom = page.getByRole('button', { name: /^Zoom to / });
  await expect(zoom).toBeVisible();
  const before = requests.length;
  await zoom.click();
  await expect.poll(() => requests.length).toBeGreaterThan(before);
  const last = requests[requests.length - 1];
  const span =
    Date.parse(last.searchParams.get('to')!) -
    Date.parse(last.searchParams.get('from')!);
  expect(span).toBeLessThan(3600_000 * 0.5);
  expect(span).toBeGreaterThan(60_000);
  await expect(page.getByText(/^Zoomed /)).toBeVisible();
  // Zooming pauses live: a fixed span cannot follow the clock.
  await expect(page.getByText('Paused', { exact: true })).toBeVisible();
});

test('a reading exactly on its limit is within limits, as the server classifies it', async ({
  page
}) => {
  // MachineSignalBinding.classify uses strict inequalities: warn_max is the
  // last acceptable reading, not the first unacceptable one - the winding
  // figure of 125 degC is "the highest reading the machine is designed to
  // produce". The page used `>=` on the same bounds, so a sensor sitting
  // exactly on its setpoint was counted over a limit here while the detector
  // called it normal, a disagreement an operator cannot resolve on screen.
  // Ten of the eleven windings count: the eleventh is pegged and bad quality.
  await mount(page, {
    signalOverrides: { '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1': 125 }
  });

  const winding = page.locator('[data-section="Motor winding temperature"]');
  await expect(winding.getByText('10 within limits')).toBeVisible();
  await expect(winding.getByText(/over a limit/)).toHaveCount(0);
});

test('a reading past its limit is counted over it', async ({ page }) => {
  await mount(page, {
    signalOverrides: { '/dex/PUMP1_PUMP_MOTOR_WINDING_TEMPERATURED1': 125.1 }
  });

  const winding = page.locator('[data-section="Motor winding temperature"]');
  await expect(
    winding.getByText('1 of 10 above warning (Warning above 125 °C)')
  ).toBeVisible();
});
