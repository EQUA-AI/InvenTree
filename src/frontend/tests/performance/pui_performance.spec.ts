import { expect, test } from '@playwright/test';

import { MACHINE, SIGNALS, mount } from './fixtures';

void MACHINE;

test('the KPI strip shows fixture values and states what it cannot show', async ({
  page
}) => {
  await mount(page);
  const power = page.locator('[data-kpi="power"]');
  await expect(power).toContainText('8.42');
  await expect(power).toContainText('MW');
  await expect(page.locator('[data-kpi="speed"]')).toContainText('745');
  await expect(page.locator('[data-kpi="vibration"]')).toContainText(
    'No approved vibration channel'
  );
  await expect(page.locator('[data-kpi="winding"]')).toContainText(
    'Highest of 11'
  );
  await expect(page.locator('[data-kpi="status"]')).toContainText('Running');
  // The one sensor without a reading is reported as poor quality, so the
  // strip is not calm - and must not claim to be.
  await expect(page.getByText('1 with poor quality')).toBeVisible();
  await expect(page.getByText('Nothing outside configured limits')).toHaveCount(
    0
  );
  await expect(
    page.getByText(`11 of ${SIGNALS.length} signals have limits configured`)
  ).toBeVisible();
});

test('families are drawn as sections with a heatmap and calculated rises', async ({
  page
}) => {
  await mount(page);
  await expect(
    page.getByText('Stator winding temperature', { exact: true })
  ).toBeVisible();
  await expect(page.getByText(/Highest now: Winding 11/)).toBeVisible();
  await expect(
    page.getByText(/Spread .* \(calculated\)/).first()
  ).toBeVisible();
  await expect(
    page.getByRole('img', { name: 'Sensor heatmap' }).first()
  ).toBeVisible();
  await expect(
    page.getByText('Cooling water rise (outlet − inlet)')
  ).toBeVisible();
  await expect(
    page.getByText('Relationship: active power and shaft speed')
  ).toBeVisible();
  // Said twice on purpose: on the KPI tile and as the section's own state.
  await expect(
    page.getByText('No approved vibration channel', { exact: true })
  ).toHaveCount(2);
  // A sensor without a reading is said to have none, not to read zero.
  await expect(page.getByText(/drawn at zero and greyed/)).toBeVisible();
});

test('the time range control changes what is asked of the server', async ({
  page
}) => {
  const requests = await mount(page);
  // An hour is 720 snapshots, past the complete-read bound, so the default
  // window is sampled; fifteen minutes is read whole.
  await expect(page.getByText(/Sampled: one reading every/)).toBeVisible();
  await page.getByText('15 min', { exact: true }).click();
  await expect(page.getByText(/Every reading in the window/)).toBeVisible();
  const before = requests.length;
  await page.getByText('6 h', { exact: true }).click();
  await expect.poll(() => requests.length).toBeGreaterThan(before);
  const last = requests[requests.length - 1];
  const span =
    Date.parse(last.searchParams.get('to')!) -
    Date.parse(last.searchParams.get('from')!);
  expect(Math.round(span / 3600_000)).toBe(6);
  await expect(page.getByText(/Sampled: one reading every/)).toBeVisible();
});

test('live can be paused and says so', async ({ page }) => {
  await mount(page);
  await expect(page.getByText('Live', { exact: true }).first()).toBeVisible();
  // Mantine's track label sits over the input; the click must go through it.
  await page.getByRole('switch').click({ force: true });
  await expect(page.getByText('Paused', { exact: true })).toBeVisible();
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
  await expect(page.getByText(/Sampled: one reading every/)).toBeVisible();
  const chart = page.locator('.recharts-wrapper').first();
  await expect(chart).toBeVisible();
  const box = (await chart.boundingBox())!;
  // Drag across the middle third of the plot area.
  const y = box.y + box.height * 0.45;
  await page.mouse.move(box.x + box.width * 0.35, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.45, y, { steps: 4 });
  await page.mouse.move(box.x + box.width * 0.6, y, { steps: 4 });
  await page.mouse.up();
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
