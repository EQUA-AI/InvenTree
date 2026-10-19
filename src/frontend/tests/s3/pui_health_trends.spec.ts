import { type Locator, type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import {
  HEALTH_SIGNALS,
  NOW,
  openHealth,
  signalTable,
  sparkline,
  trendCard
} from './healthFixtures';

nativeBaseline();
test.use({ timezoneId: 'UTC' });

/**
 * P09: the Health row graphs and full chart keep their native form while
 * every reading sits at its own time, unreadable readings and silences stay
 * open, and missing, failed, unavailable and recorded-zero records stay
 * distinct (R6 A05.3, A05.5, A05.9, A23.2; screens 10, 12, 13, 14).
 */

/** x coordinates of each subpath of an SVG path, in order. */
function subpaths(d: string): number[][] {
  return [...d.matchAll(/M([^M]+)/g)].map((match) =>
    [...match[1].matchAll(/(-?[\d.]+),(-?[\d.]+)/g)].map((point) =>
      Number(point[1])
    )
  );
}

async function rowGraphPath(page: Page, name: string) {
  const d = await sparkline(page, name).locator('path').getAttribute('d');
  expect(d).toBeTruthy();
  return d!;
}

async function chooseParameter(page: Page, label: string) {
  await trendCard(page).getByRole('combobox', { name: 'Parameter' }).click();
  await page.getByRole('option', { name: label, exact: true }).click();
}

const curve = (page: Page) => trendCard(page).locator('.recharts-area-curve');

/** Each x-axis tick's position and the instant its label names (UTC). */
async function axisTicks(card: Locator) {
  const ticks = await card
    .locator(
      '.recharts-xAxis-tick-labels text.recharts-cartesian-axis-tick-value'
    )
    .evaluateAll((nodes) =>
      nodes.map((node) => ({
        x: Number(node.getAttribute('x')),
        label: node.textContent ?? ''
      }))
    );
  return ticks.map(({ x, label }) => {
    // Ticks are whole minutes: "08:52 AM".
    const match = label.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?/);
    expect(match, `tick label ${label}`).toBeTruthy();
    let hours = Number(match![1]);
    if (match![4] === 'PM' && hours < 12) hours += 12;
    if (match![4] === 'AM' && hours === 12) hours = 0;
    const day = new Date(NOW);
    day.setUTCHours(hours, Number(match![2]), Number(match![3] ?? 0), 0);
    return { x, at: day.getTime() };
  });
}

test('row graphs place readings by time and break at silences and unreadable readings', async ({
  page
}) => {
  await openHealth(page);
  const flow = subpaths(await rowGraphPath(page, 'Discharge flow'));
  // Ten-minute window: readings for three minutes with one unreadable at
  // 100 s, silence from 180 s to 300 s, then readings to the end.
  expect(flow).toHaveLength(3);
  expect(flow[0][0]).toBe(0);
  expect(flow[0].at(-1)).toBe(19);
  expect(flow[1][0]).toBe(21);
  expect(flow[1].at(-1)).toBe(36);
  // Five minutes into ten is the middle of 120 px; index spacing put it at
  // 37 of 97 points (about 46 px) and joined it to the reading at 180 s.
  expect(flow[2][0]).toBe(60);
  expect(flow[2].at(-1)).toBe(120);
  // The native form is unchanged around the corrected path.
  const graph = sparkline(page, 'Discharge flow');
  await expect(graph).toHaveAttribute('width', '120');
  await expect(graph).toHaveAttribute('height', '28');
  await expect(graph.locator('path')).toHaveAttribute('stroke-width', '1.5');
});

test('the full chart keeps the silence open on a proportional time axis', async ({
  page
}, testInfo) => {
  for (const scheme of ['light', 'dark'] as const) {
    await openHealth(page, { scheme });
    const card = trendCard(page);
    await expect(curve(page)).toHaveCount(1);
    await expect(card.getByText('98 samples')).toBeVisible();
    const line = subpaths((await curve(page).getAttribute('d'))!);
    // Broken after the unreadable reading and across the silence.
    expect(line).toHaveLength(3);

    // Recharts nudges the final label inward to fit, so the mapping comes
    // from the first two ticks and only interior ticks are compared.
    const ticks = (await axisTicks(card)).slice(0, -1);
    expect(ticks.length).toBeGreaterThanOrEqual(2);
    const [first, second] = ticks;
    const pxPerMs = (second.x - first.x) / (second.at - first.at);
    const xAt = (ms: number) => first.x + (ms - first.at) * pxPerMs;
    // Ticks are evenly spaced in time, so the axis is proportional.
    for (const tick of ticks) {
      expect(Math.abs(tick.x - xAt(tick.at))).toBeLessThan(1.5);
    }
    // The line spans the whole selected window.
    expect(Math.abs(line[0][0] - xAt(NOW - 600_000))).toBeLessThan(1.5);
    expect(Math.abs(line[2].at(-1)! - xAt(NOW))).toBeLessThan(1.5);
    // The second run ends at 08:53:00 and the third starts at 08:55:00.
    expect(Math.abs(line[1].at(-1)! - xAt(NOW - 420_000))).toBeLessThan(1.5);
    expect(Math.abs(line[2][0] - xAt(NOW - 300_000))).toBeLessThan(1.5);

    const gaps = card
      .getByRole('alert')
      .filter({ hasText: 'Gaps in this record' });
    await expect(gaps).toContainText(
      "Silences longer than the source's 5-second cadence: 1."
    );
    await expect(gaps).toContainText('Readings that are not numbers: 1 of 98.');
    await expect(gaps).toContainText('Nothing is filled in');
    await expect(card).toContainText(
      'Selected window: Oct 18, 2026, 08:50:00 AM to Oct 18, 2026, 09:00:00 AM (UTC).'
    );
    // The controls stay inside the card at every width.
    const cardBox = (await card.boundingBox())!;
    const parameterBox = (await card
      .getByRole('combobox', { name: 'Parameter' })
      .boundingBox())!;
    expect(parameterBox.x + parameterBox.width).toBeLessThanOrEqual(
      cardBox.x + cardBox.width
    );
    // The native blue area and controls are kept.
    await expect(curve(page)).toHaveAttribute('stroke', /blue-6/);
    await expect(card.getByRole('combobox', { name: 'Range' })).toHaveValue(
      '10 minutes'
    );
    await card.screenshot({
      path: testInfo.outputPath(`chart-silence-${scheme}.png`),
      animations: 'disabled'
    });
  }
});

test('recorded zero, empty read, no source history and a missing row stay distinct', async ({
  page
}, testInfo) => {
  await openHealth(page);
  const table = signalTable(page);
  // A recorded zero is a drawn line along the floor, not a gap.
  expect(subpaths(await rowGraphPath(page, 'Bypass flow'))).toHaveLength(1);
  const row = (name: string) =>
    table.getByRole('row').filter({ hasText: name });
  await expect(row('Seal flush flow')).toContainText('No samples');
  await expect(row('Cooling water flow')).toContainText('No trend');
  await expect(row('Winding temperature')).toContainText('No trend');

  await chooseParameter(page, 'Bypass flow (m3/h)');
  await expect(curve(page)).toHaveCount(1);
  await chooseParameter(page, 'Seal flush flow (l/min)');
  await expect(trendCard(page)).toContainText('No samples in this window');
  await expect(trendCard(page)).toContainText('and from a recorded zero');
  await chooseParameter(page, 'Cooling water flow (m3/h)');
  await expect(trendCard(page)).toContainText('No history for this signal');
  await expect(trendCard(page)).toContainText('no configured connector');
  await table.screenshot({
    path: testInfo.outputPath('table-states.png'),
    animations: 'disabled'
  });
});

test('a failed read says it failed, in the rows and in the chart', async ({
  page
}) => {
  await openHealth(page, { batchStatus: 503, chartStatus: 503 });
  const drawable = HEALTH_SIGNALS.length;
  await expect(
    signalTable(page).getByText('Read failed', { exact: true })
  ).toHaveCount(drawable);
  await expect(signalTable(page).getByText('No trend')).toHaveCount(0);
  await expect(trendCard(page)).toContainText('Could not load the trend');
  // Current readings are kept beside the failure.
  await expect(
    signalTable(page).getByRole('cell', { name: '0 m3/h', exact: true })
  ).toBeVisible();
});

test('a raw unitless vibration channel keeps its own units', async ({
  page
}) => {
  await openHealth(page);
  await chooseParameter(page, 'Motor DE vibration (raw)');
  await expect(curve(page)).toHaveCount(1);
  await expect(page.getByText(/mm\/s/)).toHaveCount(0);
  await expect(
    signalTable(page).getByRole('cell', { name: '1834', exact: true })
  ).toBeVisible();
  await expect(trendCard(page).locator('.recharts-label')).toHaveCount(0);
});

test('a server that states no cadence breaks only at unreadable readings, and says so', async ({
  page
}) => {
  await openHealth(page, { cadence: false });
  expect(subpaths(await rowGraphPath(page, 'Discharge flow'))).toHaveLength(2);
  await expect(trendCard(page)).toContainText(
    'This source did not state its sampling interval'
  );
});
