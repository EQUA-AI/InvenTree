import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { captureEvidence } from './captureEvidence';
import {
  captureFromTop,
  mockEquipment,
  openEquipment,
  openExplorer
} from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';
nativeBaseline();
nativeThemeFixtures();
for (const scheme of ['light', 'dark'] as const)
  test(`keyboard focus sheet ${scheme}`, async ({ page }, info) => {
    test.skip(info.project.name.endsWith('phone'), 'Focus sheet at 1440');
    test.setTimeout(180000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await mockEquipment(page);
    await openEquipment(page, false, scheme);
    for (const [state, selector] of [
      ['canvas', '.aimms-twin-canvas'],
      ['hotspot', '.aimms-twin-hotspot:not([hidden])'],
      ['segmented-control', '.mantine-SegmentedControl-input']
    ] as const) {
      await page.keyboard.press('Tab');
      await page.locator(selector).first().focus();
      await captureFromTop(page);
      await expect(page.locator(selector).first()).toBeFocused();
      await captureEvidence(
        page,
        info,
        'equipment-model',
        `focus-${state}`,
        scheme,
        1440
      );
    }
    await mockExplorer(page);
    await openExplorer(page, '/machines/index/sites/?location=12', scheme);
    for (const [state, control] of [
      [
        'row-link',
        page.getByRole('link', { name: 'Synthetic pump 001', exact: true })
      ],
      ['switch', page.getByRole('switch', { name: 'Include sublocations' })]
    ] as const) {
      await page.keyboard.press('Tab');
      await control.focus();
      await captureFromTop(page);
      await expect(control).toBeFocused();
      await captureEvidence(
        page,
        info,
        'machines',
        `focus-${state}`,
        scheme,
        1440
      );
    }
    await mockEquipment(page);
    await openEquipment(page, true, scheme, { role: 'acknowledger' });
    const ack = page.getByRole('button', {
      name: 'Acknowledge Synthetic alarm 1',
      exact: true
    });
    await page.keyboard.press('Tab');
    await ack.focus();
    await captureEvidence(
      page,
      info,
      'equipment-schematic',
      'focus-acknowledge',
      scheme,
      1440
    );
  });
