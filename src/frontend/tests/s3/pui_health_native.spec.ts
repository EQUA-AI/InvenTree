import { writeFile } from 'node:fs/promises';
import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { openHealth, signalTable, trendCard } from './healthFixtures';

nativeBaseline();
test.use({ timezoneId: 'UTC' });

/**
 * What the native Health tab draws, recorded per run so before and after
 * renders can be compared form by form (R6 A05.1, A05.2, A05.8).
 */
async function inventory(page: Page) {
  return page.evaluate(() => {
    const svgs = [...document.querySelectorAll('table svg[role="img"]')];
    const area = document.querySelector('.recharts-area-area');
    const curve = document.querySelector('.recharts-area-curve');
    return {
      headings: [...document.querySelectorAll('h4')].map((h) => h.textContent),
      columns: [...document.querySelectorAll('table')][0]
        ? [
            ...[...document.querySelectorAll('table')][0].querySelectorAll(
              'thead th'
            )
          ].map((th) => th.textContent)
        : [],
      rowGraphs: svgs.map((svg) => ({
        label: svg.getAttribute('aria-label'),
        width: svg.getAttribute('width'),
        height: svg.getAttribute('height'),
        stroke: svg.querySelector('path')?.getAttribute('stroke'),
        strokeWidth: svg.querySelector('path')?.getAttribute('stroke-width'),
        opacity: svg.querySelector('path')?.getAttribute('opacity')
      })),
      fullChart: {
        present: !!curve,
        stroke: curve?.getAttribute('stroke') ?? null,
        fill: area?.getAttribute('fill') ?? null,
        height:
          document.querySelector('.recharts-responsive-container')
            ?.clientHeight ?? null
      },
      controls: [...document.querySelectorAll('label')]
        .map((label) => label.textContent)
        .filter((text) =>
          ['Parameter', 'Range', 'From', 'To'].includes(text ?? '')
        )
    };
  });
}

for (const scheme of ['light', 'dark'] as const) {
  test(`native Health forms are inventoried and captured (${scheme})`, async ({
    page
  }, testInfo) => {
    await openHealth(page, { scheme });
    await expect(
      page.getByRole('img', { name: 'Recent trend for Bypass flow' })
    ).toBeVisible();
    await expect(trendCard(page).locator('.recharts-area-curve')).toHaveCount(
      1
    );

    const recorded = await inventory(page);
    expect(recorded.rowGraphs.length).toBeGreaterThan(0);
    for (const graph of recorded.rowGraphs) {
      expect(graph).toMatchObject({
        width: '120',
        height: '28',
        stroke: 'currentColor',
        strokeWidth: '1.5',
        opacity: '0.75'
      });
    }
    expect(recorded.fullChart.present).toBe(true);
    expect(recorded.fullChart.stroke).toContain('blue-6');
    expect(recorded.controls).toEqual(
      expect.arrayContaining(['Parameter', 'Range'])
    );
    await writeFile(
      testInfo.outputPath(`health-inventory-${scheme}.json`),
      JSON.stringify(recorded, null, 2)
    );

    await page.screenshot({
      path: testInfo.outputPath(`health-page-${scheme}.png`),
      fullPage: true,
      animations: 'disabled'
    });
    await signalTable(page).screenshot({
      path: testInfo.outputPath(`health-table-${scheme}.png`),
      animations: 'disabled'
    });
    await trendCard(page).screenshot({
      path: testInfo.outputPath(`health-chart-${scheme}.png`),
      animations: 'disabled'
    });
  });
}
