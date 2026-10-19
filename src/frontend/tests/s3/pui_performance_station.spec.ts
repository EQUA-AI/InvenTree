import { writeFileSync } from 'node:fs';

import { expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { BAYS, openStation } from './stationFixtures';

nativeBaseline();

/**
 * R6 P11 S3-05 / A05.7 "station comparisons and tables": the native
 * StationPerformance view keeps every form - its KPI tiles, the level and
 * running-count trend, the per-bay power and flow comparisons and the bay
 * table - and reads all of it through one keyed series request per window.
 * The inventory is attached to each run for before/after comparison.
 */
test('every native station form is drawn, and the inventory is recorded', async ({
  page
}, testInfo) => {
  for (const scheme of ['light', 'dark'] as const) {
    const requests = await openStation(page, { scheme });
    await expect(page.locator('[data-kpi="plant-power"]')).toBeVisible();
    await expect(page.locator('.recharts-surface').first()).toBeVisible();
    await page.waitForTimeout(500);
    const inventory = await page.evaluate(() => {
      const text = (el: Element) => (el.textContent ?? '').trim();
      const table = document.querySelector('table');
      return {
        kpis: [...document.querySelectorAll('[data-kpi]')].map((el) =>
          el.getAttribute('data-kpi')
        ),
        cards: [...document.querySelectorAll('p[style*="font-weight: 600"]')]
          .map(text)
          .filter(Boolean),
        charts: document.querySelectorAll('.recharts-surface').length,
        chips: [...document.querySelectorAll('.mantine-Chip-label')].map(text),
        columns: table ? [...table.querySelectorAll('thead th')].map(text) : [],
        rows: table
          ? [...table.querySelectorAll('tbody tr')].map((row) =>
              [...row.querySelectorAll('td')].map(text)
            )
          : []
      };
    });
    writeFileSync(
      testInfo.outputPath(`station-inventory-${scheme}.json`),
      `${JSON.stringify(inventory, null, 2)}\n`
    );
    await page.screenshot({
      path: testInfo.outputPath(`station-native-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
    expect(inventory.kpis).toEqual([
      'forebay',
      'pumps-running',
      'plant-power',
      'plant-flow'
    ]);
    for (const card of [
      'Forebay level and pumps running',
      'Active power by bay',
      'Discharge flow by bay'
    ])
      expect(inventory.cards).toContain(card);
    expect(inventory.charts).toBeGreaterThanOrEqual(3);
    // Bays in the plant's own numbering, not the mimic's key order.
    expect(inventory.rows.map((row) => row[0].split('Inactive')[0])).toEqual([
      'Pump 1',
      'Pump 2',
      'Pump 3',
      'Pump 10'
    ]);
    expect(inventory.columns.length).toBe(7);
    await expect(page.getByText('Inactive equipment')).toBeVisible();
    // Every state word is readable whole, at phone width too: the table
    // scrolls sideways rather than clipping its badges.
    const clipped = await page
      .locator('table .mantine-Badge-label')
      .evaluateAll((labels) =>
        labels
          .filter((label) => label.scrollWidth > label.clientWidth + 1)
          .map((label) => label.textContent)
      );
    expect(clipped).toEqual([]);
    // One keyed read for the station and all of its bays.
    expect.soft(requests).toHaveLength(1);
    const keys = (
      requests[requests.length - 1].searchParams.get('keys') ?? ''
    ).split(',');
    expect(keys).toHaveLength(4 + BAYS.length * 4);
    expect(requests.at(-1)?.searchParams.get('bindings')).toBeNull();
    // Each bay links to its own machine's Performance tab.
    await expect(
      page.getByRole('link', { name: 'Pump 10', exact: true })
    ).toHaveAttribute('href', '/machines/machine/27/performance');
  }
});
