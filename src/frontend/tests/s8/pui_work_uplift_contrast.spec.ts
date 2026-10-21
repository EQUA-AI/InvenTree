import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import {
  mockExecutionWork,
  openExecutionPanel
} from '../s4/workExecutionFixtures';
import { workScenario } from './workAccessibilityFixtures';
import { renderedTextContrast } from './workContrast';

nativeBaseline();
for (const scheme of ['light', 'dark'] as const)
  test(`Work rendered contrast ${scheme}`, async ({ page }, info) => {
    await mockExecutionWork(page, workScenario());
    await openExecutionPanel(page, 'work', scheme);
    await expect(page.getByTestId('fixture-theme-primary')).toHaveText(
      'indigo'
    );
    const ratios = await renderedTextContrast(page.getByRole('main'));
    await writeFile(
      info.outputPath('work-rendered-contrast.json'),
      JSON.stringify(ratios, null, 2)
    );
    await info.attach('rendered-contrast', {
      body: JSON.stringify(ratios, null, 2),
      contentType: 'application/json'
    });
    for (const row of ratios)
      expect(row.ratio, `${row.text} (${row.color})`).toBeGreaterThanOrEqual(
        4.5
      );
    // A real scope callout link uses the callout's readable text in both themes.
    await page.getByRole('tab', { name: 'Plan & Safety', exact: true }).click();
    const plan = await renderedTextContrast(page.getByRole('main'));
    await writeFile(
      info.outputPath('plan-rendered-contrast.json'),
      JSON.stringify(plan, null, 2)
    );
    await info.attach('plan-rendered-contrast', {
      body: JSON.stringify(plan, null, 2),
      contentType: 'application/json'
    });
    for (const row of plan)
      expect(row.ratio, `${row.text} (${row.color})`).toBeGreaterThanOrEqual(
        4.5
      );
  });
