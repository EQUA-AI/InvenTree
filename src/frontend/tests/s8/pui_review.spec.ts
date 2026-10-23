import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { mockFaultHistory } from '../s3/machineWorkFixtures';
import { readable } from './contrast';
import {
  mockEquipment,
  openEquipment,
  openExplorer,
  openFaultHistory
} from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
test.use({ timezoneId: 'Asia/Riyadh' });

for (const scheme of ['light', 'dark'] as const) {
  test(`all alarm states have readable text in ${scheme}`, async ({ page }) => {
    const data = await mockEquipment(page);
    for (const severity of ['critical', null])
      for (const status of ['open', 'acknowledged'] as const) {
        data.alarms.push({
          ...data.alarms[0],
          pointer: `/extra/${severity}/${status}`,
          severity,
          anomaly_status: status,
          anomaly: 1000 + data.alarms.length
        });
      }
    data.alarms.push({
      ...data.alarms[0],
      pointer: '/extra/unraised',
      anomaly: null
    });
    await openEquipment(page, true, scheme);
    await expect(page.locator('[data-alarm-status]')).toHaveCount(8);
    for (const badge of await page.locator('[data-alarm-status]').all())
      await readable(badge);
    await readable(page.getByText('Alarm state unavailable', { exact: true }));
    await readable(page.getByText('Not raised yet', { exact: true }));
    await expect(page.getByLabel('Process data')).toHaveCount(0);
    const information = page.getByRole('button', {
      name: 'Schematic information',
      exact: true
    });
    await page.keyboard.press('Tab');
    await information.focus();
    const provenance = page.getByRole('tooltip');
    await expect(provenance).toContainText('your clock');
    await expect(provenance).toContainText('Source: Synthetic SCADA');
    await expect(provenance).toContainText(
      'Last poll attempt: 20 Oct 2026 11:00:00'
    );
    await readable(provenance);
    await expect(page.getByText('Flow direction', { exact: true })).toHaveCount(
      0
    );
    await expect(
      page.getByText('Communication status', { exact: true })
    ).toHaveCount(0);
    await expect(page.locator('tr[data-anomaly="901"]')).toContainText(
      '20 Oct 2026 11:00:00'
    );
  });

  test(`honesty caveats and selected marker are readable in native ${scheme} theme`, async ({
    page
  }) => {
    await mockExplorer(page);
    await openExplorer(page, '/machines/index/sites/?location=12', scheme);
    const scope = page.getByTestId('machine-page-count');
    await expect(scope).toHaveAttribute('aria-label', /Page counts only/);
    await readable(scope, 14);
    const registry = page.getByRole('link', {
      name: 'Equipment Registry',
      exact: true
    });
    await expect(registry).toHaveAttribute('data-variant', 'default');
    await readable(registry);
    await readable(scope, 14);
    await mockEquipment(page);
    await openEquipment(page, false, scheme);
    await expect(
      page.getByRole('tablist', { name: 'panel-tabs-asset-machine-detail' })
    ).toBeVisible();
    const reference = page.getByRole('button', {
      name: 'Reference model',
      exact: true
    });
    await readable(reference);
    await page.keyboard.press('Tab');
    await reference.focus();
    const modelInfo = page
      .getByRole('tooltip')
      .filter({ hasText: 'Vertical reference model.' });
    await expect(modelInfo).toHaveText(
      'Vertical reference model. Use the process schematic for this machine. Drag to rotate; arrow keys rotate, + and − zoom.'
    );
    await readable(modelInfo, 14);
    await page.locator('[data-position="motor"]').click();
    await readable(page.locator('.aimms-twin-hotspot[aria-pressed="true"]'));
    await mockFaultHistory(page);
    await openFaultHistory(page, { scheme, query: '?occurrence=901' });
    const evidence = page.getByTestId('occurrence-evidence');
    await readable(
      evidence.getByText('Reading back inside its limits.', { exact: true }),
      14
    );
    await readable(
      evidence.getByRole('heading', {
        name: 'Evidence retained at the time',
        exact: true
      }),
      14
    );
    await readable(
      evidence.getByRole('link', {
        name: 'Current readings are on the Health tab',
        exact: true
      })
    );
    await expect(
      page.getByText(
        /Resolved does not always mean cleared|Retained evidence is not current readings/
      )
    ).toHaveCount(0);
  });
}

for (const role of ['viewer', 'editor', 'acknowledger', 'creator']) {
  test(`${role} sees only permitted alarm mutations`, async ({ page }) => {
    await mockEquipment(page);
    await openEquipment(page, true, 'light', { role });
    const open = page.locator('tr[data-anomaly="901"]');
    const ack = page.locator('tr[data-anomaly="902"]');
    const canChange = role === 'editor' || role === 'acknowledger';
    const canAdd = role === 'editor' || role === 'creator';
    await expect(
      open.getByRole('button', { name: /^Acknowledge Synthetic alarm/ })
    ).toHaveCount(canChange ? 1 : 0);
    for (const row of [open, ack]) {
      await expect(
        row.getByRole('button', { name: /^Dismiss Synthetic alarm/ })
      ).toHaveCount(canChange ? 1 : 0);
      await expect(
        row.getByRole('button', { name: /^Create repair for Synthetic alarm/ })
      ).toHaveCount(canAdd ? 1 : 0);
    }
    await expect(
      ack.getByRole('button', { name: /^Acknowledge Synthetic alarm/ })
    ).toHaveCount(0);
    await expect(
      open.getByRole('link', { name: 'Open occurrence for Synthetic alarm 1' })
    ).toBeVisible();
    if (canChange) {
      await open
        .getByRole('button', { name: /^Acknowledge Synthetic alarm/ })
        .click();
      await expect(page.getByRole('dialog')).toBeVisible();
      await expect(
        page
          .getByRole('dialog')
          .getByRole('button', { name: 'Acknowledge', exact: true })
      ).toBeEnabled();
      await page
        .getByRole('dialog')
        .getByRole('button', { name: 'Cancel', exact: true })
        .click();
      await open
        .getByRole('button', { name: /^Dismiss Synthetic alarm/ })
        .click();
      await expect(
        page
          .getByRole('dialog')
          .getByRole('button', { name: 'Dismiss', exact: true })
      ).toBeDisabled();
    }
  });
}

test('the selected location chooser keeps the tree and results in step across the theme lg boundary', async ({
  page
}) => {
  await mockExplorer(page);
  await openExplorer(page, '/machines/index/sites/?location=12');
  for (const width of [1183, 1184, 1190, 1199, 1200]) {
    await page.setViewportSize({ width, height: 900 });
    const tree = page.locator('#machine-location-browser');
    const disclosure = page.getByRole('button', { name: 'Choose location' });
    await expect(tree).toBeHidden();
    await expect(disclosure).toHaveAttribute('aria-expanded', 'false');
    await disclosure.click();
    await expect(tree).toBeVisible();
    await expect(disclosure).toHaveAttribute('aria-expanded', 'true');
    const treeBox = (await tree.boundingBox())!;
    const summaryBox = (await page
      .getByRole('region', { name: 'Loaded page summary' })
      .boundingBox())!;
    if (width < 1184) {
      expect(summaryBox.y).toBeGreaterThanOrEqual(
        treeBox.y + treeBox.height - 1
      );
    } else {
      expect(summaryBox.x).toBeGreaterThanOrEqual(
        treeBox.x + treeBox.width - 1
      );
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(width);
    await disclosure.click();
    await expect(tree).toBeHidden();
  }
});

test('supplied plant timezone is used for alarm observations', async ({
  page
}) => {
  const data = await mockEquipment(page);
  data.plant_timezone = 'America/Chicago';
  await openEquipment(page, true, 'light');
  const row = page.locator('tr[data-anomaly="901"]');
  await page.keyboard.press('Tab');
  await page
    .getByRole('button', { name: 'Schematic information', exact: true })
    .focus();
  await expect(page.getByRole('tooltip')).toContainText(
    'plant clock (America/Chicago'
  );
  await expect(row).toContainText('20 Oct 2026 03:00:00');
});

for (const scheme of ['light', 'dark'] as const)
  test(`selected marker follows readable blue user preference in ${scheme}`, async ({
    page
  }) => {
    await mockEquipment(page);
    await openEquipment(page, false, scheme, { primary: 'blue' });
    await page.locator('[data-position="motor"]').click();
    await expect(
      page.locator('.aimms-twin-hotspot[aria-pressed="true"]')
    ).toHaveCSS('background-color', 'rgb(25, 113, 194)');
    await readable(page.locator('.aimms-twin-hotspot[aria-pressed="true"]'));
  });
