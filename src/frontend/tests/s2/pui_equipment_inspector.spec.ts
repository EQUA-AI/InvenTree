import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { mockPositions, positionsFixture } from './equipmentFixtures';

nativeBaseline();

async function openPanel(page: Page, scheme: 'light' | 'dark' = 'light') {
  await page.goto(`/playwright/s2-equipment.html?kind=panel&scheme=${scheme}`);
  await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible();
}

// A labelled region, so assistive tech can find and name it.
const inspector = (page: Page) =>
  page.getByRole('region', { name: 'Selected position', exact: true });
const pick = (page: Page, id: string) =>
  page.locator(`[data-position="${id}"]`).click();

test('a bound position shows its own slot, reading and work, and the rest as parent context', async ({
  page
}, testInfo) => {
  for (const scheme of ['light', 'dark'] as const) {
    await mockPositions(page);
    await openPanel(page, scheme);
    await pick(page, 'bearing');
    const panel = inspector(page);
    await expect(
      panel.getByRole('heading', { name: 'Motor drive-end bearing' })
    ).toBeVisible();
    await expect(panel).toContainText(
      'Catalogue family: Motor Drive-End Bearing (MOTOR-DE-BRG)'
    );
    const slot = panel.locator('[data-slot="PS-MOTOR-DE-BRG:1"]');
    await expect(slot).toContainText('Draft inference');
    await expect(slot).toContainText('physical assignment is unverified');
    const reading = panel.locator('[data-signal="202"]');
    await expect(reading).toContainText('DE bearing temperature');
    await expect(reading).toContainText('95 degC');
    await expect(reading).toContainText('Warning');
    await expect(panel.locator('[data-position-work]')).toHaveText(
      '1 open on this component'
    );
    await expect(
      panel.getByRole('link', { name: 'WO-73', exact: true })
    ).toHaveAttribute('href', '/maintenance/work-orders/73/');
    await expect(panel.locator('[data-parent-work]')).toHaveText(
      "2 other open on this machine (parent context, not this position's)."
    );
    // Every reading carries its own age, and the read itself its time.
    await expect(reading).toContainText('ago');
    await expect(panel.locator('[data-records-read]')).toContainText(
      'Records read at'
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
    await page.screenshot({
      path: testInfo.outputPath(`inspector-bearing-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  }
});

test('another position changes only the bound context, from one read', async ({
  page
}) => {
  const positions = await mockPositions(page);
  await openPanel(page);
  const panel = inspector(page);
  // Impeller: no family, so nothing of the bearing's appears.
  await pick(page, 'bearing');
  await pick(page, 'impeller');
  await expect(panel).toContainText(
    'Not bound: this reference position has no catalogue component family, so no records are shown for it.'
  );
  await expect(panel.getByRole('link', { name: 'WO-73' })).toHaveCount(0);
  await expect(panel.locator('[data-signal]')).toHaveCount(0);
  await expect(panel.locator('[data-parent-work]')).toHaveText(
    "3 open on the whole machine (parent context, not this position's)."
  );
  // Motor: its own verified slot and reading, and none of the bearing's work.
  await pick(page, 'motor');
  await expect(panel.locator('[data-slot="PS-MOTOR:1"]')).toContainText(
    'Verified'
  );
  await expect(panel.locator('[data-signal="201"]')).toContainText('Normal');
  await expect(panel).toContainText('No open work on this component');
  await expect(panel.locator('[data-parent-work]')).toHaveText(
    "3 other open on this machine (parent context, not this position's)."
  );
  // Casing: a family with no slot on this machine.
  await pick(page, 'pump');
  await expect(panel).toContainText(
    'No Pump Casing and Spiral Case slot is recorded for this machine, so no readings or work are shown.'
  );
  // A click on the model chooses the same position and the same records.
  await page
    .getByRole('button', { name: '1. Select electric motor', exact: true })
    .click();
  await expect(panel.locator('[data-slot="PS-MOTOR:1"]')).toBeVisible();
  expect(positions.reads()).toBe(1);
});

test('the whole machine shows its work and asks for a component', async ({
  page
}) => {
  await mockPositions(page);
  await openPanel(page);
  const panel = inspector(page);
  await expect(
    panel.getByRole('heading', { name: 'Complete pump train' })
  ).toBeVisible();
  await expect(panel.locator('[data-machine-work]')).toHaveText(
    '3 open on this machine'
  );
  await expect(panel).toContainText(
    'Select a component to see its own slot, readings and work.'
  );
});

test('records that cannot be loaded show nothing cached and can be retried', async ({
  page
}) => {
  let fail = true;
  await page.route('**/api/assets/machines/18/positions/', (route) =>
    fail
      ? route.fulfill({ status: 503, json: { detail: 'Synthetic' } })
      : route.fulfill({ json: positionsFixture() })
  );
  await openPanel(page);
  await pick(page, 'bearing');
  const panel = inspector(page);
  await expect(panel).toContainText(
    "This machine's component records could not be loaded. Nothing cached is shown."
  );
  await expect(panel.locator('[data-slot]')).toHaveCount(0);
  // The model and list keep working while the records are down.
  await expect(page.locator('[data-selected-position]')).toHaveText(
    'Selected: Motor drive-end bearing'
  );
  fail = false;
  await panel.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(panel.locator('[data-slot="PS-MOTOR-DE-BRG:1"]')).toBeVisible();
});

test('work that cannot be checked is unknown, not zero', async ({ page }) => {
  const data = positionsFixture({ machine_work: null });
  const bearing = (data.positions as { id: string; work: unknown }[]).find(
    (position) => position.id === 'bearing'
  )!;
  bearing.work = null;
  await page.route('**/api/assets/machines/18/positions/', (route) =>
    route.fulfill({ json: data })
  );
  await openPanel(page);
  await pick(page, 'bearing');
  const panel = inspector(page);
  await expect(panel).toContainText('Work could not be checked.');
  await expect(panel.locator('[data-parent-work]')).toHaveText(
    'Machine work could not be checked.'
  );
  await expect(panel.locator('[data-position-work]')).toHaveCount(0);
});

test("a component's owning job is one activation away", async ({ page }) => {
  await mockPositions(page);
  await openPanel(page);
  await pick(page, 'bearing');
  await inspector(page)
    .getByRole('link', { name: 'WO-73', exact: true })
    .click();
  await expect(page.getByTestId('fixture-location')).toHaveText(
    '/maintenance/work-orders/73/'
  );
});

test('readings print at a sensible precision and say when their quality is poor', async ({
  page
}) => {
  const data = positionsFixture();
  const bearing = (
    data.positions as { id: string; signals: Record<string, unknown>[] }[]
  ).find((position) => position.id === 'bearing')!;
  bearing.signals = [
    {
      ...bearing.signals[0],
      value: 95.123456,
      quality: 'uncertain',
      state: 'unknown'
    }
  ];
  await page.route('**/api/assets/machines/18/positions/', (route) =>
    route.fulfill({ json: data })
  );
  await openPanel(page);
  await pick(page, 'bearing');
  const reading = inspector(page).locator('[data-signal="202"]');
  await expect(reading).toContainText('95.12 degC');
  await expect(reading).not.toContainText('95.123456');
  await expect(reading.locator('[data-quality]')).toHaveText(
    'Quality: Uncertain'
  );
});

test('records the reader may not see say so, with no retry that cannot help', async ({
  page
}) => {
  await page.route('**/api/assets/machines/18/positions/', (route) =>
    route.fulfill({ status: 403, json: { detail: 'Synthetic denial' } })
  );
  await openPanel(page);
  await pick(page, 'bearing');
  const panel = inspector(page);
  await expect(panel).toContainText('Records not available to you');
  await expect(panel.getByRole('button', { name: 'Retry' })).toHaveCount(0);
  await expect(panel.locator('[data-slot]')).toHaveCount(0);
});
