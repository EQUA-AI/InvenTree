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
  await openPerformance(page, {
    harness: 's3',
    delaySeriesMs: (_url, index) => (index >= 1 ? 2500 : 0)
  });
  await expect(status(page)).toHaveAttribute('data-window-status', 'current');
  await expect(status(page)).toContainText('Window on screen:');
  await expect(status(page)).toContainText('(1 h)');
  await expect(status(page)).toContainText(/Times in .*UTC[+-]\d\d:\d\d/);

  await page.getByText('6 h', { exact: true }).click();
  await expect(status(page)).toHaveAttribute('data-window-status', 'updating');
  await expect(status(page)).toContainText('Updating');
  await expect(status(page)).toContainText(
    /Charts still show the previous window, .* \(1 h\), until the new one arrives\./
  );
  await page.screenshot({
    path: testInfo.outputPath('pump-updating.png'),
    animations: 'disabled'
  });
  await expect(status(page)).toHaveAttribute('data-window-status', 'current', {
    timeout: 10_000
  });
  await expect(status(page)).toContainText('(6 h)');
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
  await expect(status(page)).toContainText('(1 h)');
  await page.getByText('6 h', { exact: true }).click();
  await page.getByText('15 min', { exact: true }).click();
  await expect(status(page)).toContainText('(15 min)');
  await expect(page.getByText(/Every reading in the window/)).toBeVisible();
  // Let the slow 6 h answer land; the page stays on what was chosen last.
  await expect.poll(() => requests.length).toBeGreaterThanOrEqual(3);
  await page.waitForTimeout(3500);
  await expect(status(page)).toContainText('(15 min)');
  await expect(status(page)).toHaveAttribute('data-window-status', 'current');
  await expect(page.getByText(/Every reading in the window/)).toBeVisible();
});

const bayCard = (page: Page, title: string) =>
  page
    .locator('.mantine-Paper-root')
    .filter({ has: page.getByText(title, { exact: true }) })
    .first();

test('a station whose source serves nothing says so and keeps its bay cards', async ({
  page
}, testInfo) => {
  await openStation(page, { fail: true });
  await expect(
    page.getByText('The source could not serve this window')
  ).toBeVisible();
  // Nothing was read, so nothing is live.
  await expect(
    page.locator('.mantine-Badge-root').filter({ hasText: /^No readings$/ })
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
  await expect(page.locator('[data-kpi="plant-power"]')).toContainText(
    "sum of 3 of 4 bays' newest readings"
  );
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
    await expect(
      page.getByText(
        /Each bay's newest usable reading in the window on screen \(.*\); window peak over the same span\./
      )
    ).toBeVisible();
    // Pump 2 has no status reading in the window: its state is the
    // station's present word for it, and says so.
    const row = page.locator('tbody tr').filter({ hasText: 'Pump 2' });
    await expect(row).toContainText('Current, not from this window');
    await page.screenshot({
      path: testInfo.outputPath(`station-window-labels-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
  }
  // Paused, the window stops at the moment it was frozen.
  await page.getByRole('switch').click({ force: true });
  await expect(page.getByText('Paused', { exact: true })).toBeVisible();
  await expect(page.locator('[data-kpi="plant-power"]')).toContainText(
    "bays' last readings in the selected window"
  );
  await expect(
    page.getByText(
      /Each bay's last usable reading in the selected window \(.*\), not its present value/
    )
  ).toBeVisible();
  await expect(status(page)).toContainText('Window on screen:');
});
