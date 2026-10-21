import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { mockFaultHistory } from '../s3/machineWorkFixtures';
import { textSweep } from './contrast';
import {
  mockEquipment,
  openEquipment,
  openExplorer,
  openFaultHistory
} from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';
nativeBaseline();
nativeThemeFixtures();
for (const scheme of ['light', 'dark'] as const)
  for (const panel of ['machines', 'model', 'schematic', 'faults'])
    test(`${panel} full text contrast ${scheme}`, async ({ page }, info) => {
      if (panel === 'machines') {
        await mockExplorer(page);
        await openExplorer(page, '/machines/index/sites/?location=12', scheme);
      } else if (panel === 'faults') {
        await mockFaultHistory(page);
        await openFaultHistory(page, { scheme, query: '?occurrence=901' });
      } else {
        await mockEquipment(page);
        await openEquipment(page, panel === 'schematic', scheme, {
          role: 'acknowledger'
        });
      }
      const scope = page.locator('[data-operational-panel]').first();
      const results = await textSweep(scope);
      await info.attach('text-contrast-sweep', {
        body: JSON.stringify(results, null, 2),
        contentType: 'application/json'
      });
      expect(
        results.filter((item) => item.ratio < 4.5),
        JSON.stringify(results.filter((item) => item.ratio < 4.5))
      ).toEqual([]);
    });
