import { writeFileSync } from 'node:fs';

import { expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { openPerformance } from './performanceFixtures';

nativeBaseline();

/**
 * R6 P11 / A05.7 / A19.1: every native Performance form stays drawn. The
 * inventory is attached to each run so a change can be compared form by
 * form against the run before it, alongside the full-page renders.
 */
test('every native Performance form is drawn, and the inventory is recorded', async ({
  page
}, testInfo) => {
  for (const scheme of ['light', 'dark'] as const) {
    await openPerformance(page, { scheme });
    await expect(page.locator('[data-kpi="power"]')).toContainText('8.42');
    // Let every chart settle before counting what was drawn.
    await expect(page.locator('.recharts-surface').first()).toBeVisible();
    await page.waitForTimeout(500);
    const inventory = await page.evaluate(() => {
      const text = (el: Element) => (el.textContent ?? '').trim();
      return {
        sections: [...document.querySelectorAll('[data-section]')]
          .filter((el) => el.querySelector('h5'))
          .map((el) => el.getAttribute('data-section')),
        // Card titles are the bold lines inside each section.
        cards: [...document.querySelectorAll('[data-section]')].map(
          (section) => ({
            section: section.getAttribute('data-section'),
            titles: [
              ...section.querySelectorAll('p[style*="font-weight: 600"]')
            ].map(text)
          })
        ),
        kpis: [...document.querySelectorAll('[data-kpi]')].map((el) =>
          el.getAttribute('data-kpi')
        ),
        otherRows: document.querySelectorAll(
          '[data-section="Other signals"] tbody tr'
        ).length,
        heatmaps: document.querySelectorAll('[aria-label="Sensor heatmap"]')
          .length,
        charts: document.querySelectorAll('.recharts-surface').length
      };
    });
    writeFileSync(
      testInfo.outputPath(`performance-inventory-${scheme}.json`),
      `${JSON.stringify(inventory, null, 2)}\n`
    );
    // The forms R6 names, as this fixture carries them.
    for (const section of [
      'Electrical',
      'Speed, operation and hydraulics',
      'Motor winding temperature',
      'Vibration and mechanical',
      'Cooling and thermal',
      'Bearing and lubrication',
      'Other signals'
    ])
      expect(inventory.sections).toContain(section);
    for (const card of [
      'Power and power factor',
      'Excitation',
      'Frequency',
      'Shaft speed and status',
      'Stator winding temperature',
      'Cooling water rise (outlet − inlet)',
      'Thrust bearing pads'
    ])
      await expect(page.getByText(card, { exact: true }).first()).toBeVisible();
    // Unmapped tags stay reachable in the Other table.
    expect(inventory.otherRows).toBeGreaterThan(0);
    await expect(
      page.getByText('/dex/PUMP1_SPARE_INSTRUMENT_7', { exact: true })
    ).toBeVisible();
    expect(inventory.heatmaps).toBeGreaterThan(0);
    await expect(
      page.getByText('Relationship: active power and shaft speed')
    ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath(`performance-native-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
    // The top of the tab: controls, KPIs, the verdict strip and Electrical.
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: testInfo.outputPath(`performance-top-${scheme}.png`),
      animations: 'disabled'
    });
  }
});
