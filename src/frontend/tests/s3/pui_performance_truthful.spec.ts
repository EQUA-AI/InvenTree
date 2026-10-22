import { historyInformation, samplingInformation } from './presentationAssertions';
import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { openPerformance } from './performanceFixtures';
import { BAYS, type BaySpec, openStation } from './stationFixtures';

nativeBaseline();

/**
 * R6 P11 S3-06 / A19.9 / A19.11: the Performance tab says which window its
 * charts are drawing and in whose clock, says when that is still the
 * previous window, never lets a late answer replace a later choice, and
 * keeps the station's current-versus-window origins and missing history
 * explicit.
 */

const status = (page: Page) => page.locator('[data-window-status]');

test('the charts say which window they draw, and when it is still the previous one', async ({
  page
}, testInfo) => {
  const requests = await openPerformance(page, {
    harness: 's3',
    delaySeriesMs: (_url, index) => (index >= 1 ? 2500 : 0)
  });
  await expect(status(page)).toHaveAttribute('data-window-status', 'current');
  expect(Date.parse(requests[0].searchParams.get('to')!) - Date.parse(requests[0].searchParams.get('from')!)).toBe(3600_000);
  await expect(status(page)).not.toContainText('Window on screen:');
  const previousCurve = await page.locator('.recharts-line-curve').first().getAttribute('d');
  const clock = page
    .getByTestId('pump-performance')
    .getByText(/Times: your clock .*UTC[+-]\d\d:\d\d/);
  await expect(clock).toHaveCount(0);
  await historyInformation(page);
  await expect(status(page)).not.toContainText('Times in');

  await page.getByText('6 h', { exact: true }).click();
  await expect(status(page)).toHaveAttribute('data-window-status', 'updating');
  await expect(status(page)).toContainText('Updating');
  await expect(page.locator('.recharts-line-curve').first()).toHaveAttribute('d', previousCurve!);
  await expect(status(page).locator('output')).toHaveText('Updating selected history window.');
  await page.screenshot({
    path: testInfo.outputPath('pump-updating.png'),
    animations: 'disabled'
  });
  await expect(status(page)).toHaveAttribute('data-window-status', 'current', {
    timeout: 10_000
  });
  expect(Date.parse(requests.at(-1)!.searchParams.get('to')!) - Date.parse(requests.at(-1)!.searchParams.get('from')!)).toBe(6 * 3600_000);
});

test('a late answer for an earlier window never replaces the window chosen after it', async ({
  page
}) => {
  const requests = await openPerformance(page, {
    harness: 's3',
    // The 6 h read is slow; the 15 min read chosen after it is quick.
    delaySeriesMs: (url) => {
      const span =
        Date.parse(url.searchParams.get('to')!) -
        Date.parse(url.searchParams.get('from')!);
      return span > 3 * 3600_000 ? 3000 : 0;
    }
  });
  expect(Date.parse(requests[0].searchParams.get('to')!) - Date.parse(requests[0].searchParams.get('from')!)).toBe(3600_000);
  await page.getByText('6 h', { exact: true }).click();
  await page.getByText('15 min', { exact: true }).click();
  await expect(status(page)).toHaveAttribute('data-window-status', 'current');
  expect(Date.parse(requests.at(-1)!.searchParams.get('to')!) - Date.parse(requests.at(-1)!.searchParams.get('from')!)).toBe(900_000);
  await samplingInformation(page, 'Every reading in the window');
  // Let the slow 6 h answer land; the page stays on what was chosen last.
  await expect.poll(() => requests.length).toBeGreaterThanOrEqual(3);
  await page.waitForTimeout(3500);
  await expect(status(page)).toHaveAttribute('data-window-status', 'current');
  expect(Date.parse(requests.at(-1)!.searchParams.get('to')!) - Date.parse(requests.at(-1)!.searchParams.get('from')!)).toBe(900_000);
  await expect(status(page)).toHaveAttribute('data-window-status', 'current');
  await samplingInformation(page, 'Every reading in the window');
});

const bayCard = (page: Page, title: string) =>
  page
    .locator('.mantine-Paper-root')
    .filter({ has: page.getByText(title, { exact: true }) })
    .first();

test('a station whose source serves nothing says so and keeps its bay cards', async ({
  page
}, testInfo) => {
  await openStation(page, { fail: true, recordedTelemetryAsLive: false });
  await expect(
    page.getByText('The source could not serve this window')
  ).toBeVisible();
  // Nothing was read, so nothing is live.
  await expect(
    page.getByRole('switch', { name: 'Live updates: No readings', exact: true })
  ).toBeVisible();
  await expect(
    page.locator('.mantine-Badge-root').filter({ hasText: /^Live$/ })
  ).toHaveCount(0);
  for (const title of ['Active power by bay', 'Discharge flow by bay']) {
    const card = bayCard(page, title);
    await expect(card).toBeVisible();
    await expect(card).toContainText(
      'No history in this window for Pump 1, Pump 2, Pump 3, Pump 10.'
    );
    await expect(card).toContainText('No readings in this window.');
  }
  await page.screenshot({
    path: testInfo.outputPath('station-no-history.png'),
    fullPage: true,
    animations: 'disabled'
  });
  await openStation(page, { fail: true, recordedTelemetryAsLive: true });
  await expect(
    page.getByTestId('live-indicator').locator('[data-tone="live"]')
  ).toHaveCount(0);
  await expect(page.getByTestId('live-indicator')).toContainText(
    'Readings unavailable'
  );
  await expect(
    page.getByText('The source could not serve this window')
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('station-unavailable-live.png'),
    fullPage: true,
    animations: 'disabled'
  });
});

test('a bay without history is named, and the plant total says how many bays it sums', async ({
  page
}) => {
  const bays: BaySpec[] = BAYS.map((bay) =>
    bay.key === 'P3' ? { ...bay, noHistory: true } : bay
  );
  await openStation(page, { bays });
  await expect(
    page.getByText('No history in this window for Pump 3.')
  ).toHaveCount(2);
  const total = page.locator('[data-kpi="plant-power"]');
  const missing = total.getByRole('button', { name: 'Plant power: 1 of 4 bays not reporting', exact: true });
  await expect(missing).toHaveText('—');
  await page.keyboard.press('Tab');
  await missing.focus();
  await expect(page.getByRole('tooltip')).toHaveText('Plant power: 1 of 4 bays not reporting');
  await expect(
    page.getByText('The source could not serve this window')
  ).toHaveCount(0);
});

test('station values say they are the window’s, and a paused window is not the present', async ({
  page
}, testInfo) => {
  const bays: BaySpec[] = BAYS.map((bay) =>
    bay.key === 'P2' ? { ...bay, status: null } : bay
  );
  for (const scheme of ['light', 'dark'] as const) {
    await openStation(page, { bays, scheme });
    await expect(page.getByText(/Each bay's newest usable reading/)).toHaveCount(0);
    await historyInformation(page);
    // Pump 2 has no status reading in the window. The mimic's response
    // timestamp is separate from any source observation timestamp.
    const row = page.locator('tbody tr').filter({ hasText: 'Pump 2' });
    await expect(row).toContainText(
      /Station mimic read at .*not an observation time.*not from this window/
    );
    await page.screenshot({
      path: testInfo.outputPath(`station-window-labels-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  }
  // Paused, the window stops at the moment it was frozen.
  await page.getByRole('switch', { name: /^Live updates:/ }).click({ force: true });
  await expect(page.getByText('Paused', { exact: true })).toBeVisible();
  await page.locator('[data-kpi="plant-power"]').getByText('Plant power', { exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText("bays' last readings in the selected window");
  await expect(page.getByText(/Each bay's last usable reading in the selected window/)).toHaveCount(0);
  await expect(status(page)).toHaveAttribute('data-window-status', 'current');
  await expect(page.getByRole('switch', { name: 'Live updates: Paused', exact: true })).not.toBeChecked();
});
