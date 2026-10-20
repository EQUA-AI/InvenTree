import { type Locator, expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { mockFaultHistory } from '../s3/machineWorkFixtures';
import {
  mockEquipment,
  openEquipment,
  openExplorer,
  openFaultHistory
} from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();

/** WCAG relative luminance of the actual rendered foreground and background.
 * Composite transparent ancestor backgrounds, including striped table rows. */
async function contrast(locator: Locator) {
  return locator.evaluate((element) => {
    type RGB = [number, number, number, number];
    const rgba = (value: string): RGB => {
      const components = value.match(/[\d.]+/g)!.map(Number);
      return [components[0], components[1], components[2], components[3] ?? 1];
    };
    const over = (front: RGB, back: RGB): RGB => [
      front[0] * front[3] + back[0] * (1 - front[3]),
      front[1] * front[3] + back[1] * (1 - front[3]),
      front[2] * front[3] + back[2] * (1 - front[3]),
      1
    ];
    const chain: Element[] = [];
    for (let node: Element | null = element; node; node = node.parentElement)
      chain.unshift(node);
    let background: RGB = [255, 255, 255, 1];
    for (const node of chain)
      background = over(
        rgba(getComputedStyle(node).backgroundColor),
        background
      );
    const style = getComputedStyle(element);
    const foreground = over(rgba(style.color), background);
    const luminance = (color: RGB) =>
      color.slice(0, 3).reduce((sum, value, index) => {
        const s = value / 255;
        return (
          sum +
          (s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4) *
            [0.2126, 0.7152, 0.0722][index]
        );
      }, 0);
    const a = luminance(foreground);
    const b = luminance(background);
    return {
      ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
      foreground,
      background,
      fontSize: Number.parseFloat(style.fontSize)
    };
  });
}
async function readable(locator: Locator, minimumSize = 0) {
  await expect(locator).toBeVisible();
  const actual = await contrast(locator);
  await test.info().attach('computed-contrast', {
    body: JSON.stringify({ text: await locator.innerText(), ...actual }),
    contentType: 'application/json'
  });
  expect(actual, await locator.innerText()).toMatchObject({
    ratio: expect.any(Number)
  });
  expect(actual.ratio, JSON.stringify(actual)).toBeGreaterThanOrEqual(4.5);
  expect(actual.fontSize).toBeGreaterThanOrEqual(minimumSize);
}

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
    await expect(page.locator('[data-alarm-status]')).toHaveCount(7);
    for (const badge of await page.locator('[data-alarm-status]').all())
      await readable(badge);
    await readable(page.getByText('Alarm state unavailable', { exact: true }));
    await readable(page.getByText('Not raised yet', { exact: true }));
    await expect(
      page.getByRole('group', { name: 'Process data availability' })
    ).toBeVisible();
    await readable(page.getByText('Flow direction: unavailable'), 14);
    await readable(page.getByText('Communication status: unavailable'), 14);
    await expect(page.locator('tr[data-anomaly="901"]')).toContainText(
      '(Asia/Riyadh)'
    );
  });

  test(`honesty caveats and selected marker are readable in native ${scheme} theme`, async ({
    page
  }) => {
    await mockExplorer(page);
    await openExplorer(page, '/machines/index/sites/?location=12', scheme);
    await expect(
      page.getByRole('region', { name: 'Loaded page summary' })
    ).toHaveCSS(
      'background-color',
      await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
    );
    await readable(page.getByText(/Counts describe this page only/), 14);
    await expect(
      page.getByRole('link', { name: 'Equipment Registry', exact: true })
    ).toHaveCSS(
      'background-color',
      scheme === 'dark' ? 'rgb(59, 91, 219)' : 'rgb(76, 110, 245)'
    );
    await readable(page.getByText(/Telemetry timestamps, run \/ stop/), 14);
    await mockEquipment(page);
    await openEquipment(page, false, scheme);
    await expect(
      page.getByRole('tablist', { name: 'panel-tabs-asset-machine-detail' })
    ).toBeVisible();
    await readable(page.getByText('Reference model · geometry unverified'), 14);
    await readable(page.getByText('Reference view', { exact: true }));
    await page.locator('[data-position="motor"]').click();
    await readable(page.locator('.aimms-twin-hotspot[aria-pressed="true"]'));
    await mockFaultHistory(page);
    await openFaultHistory(page, { scheme });
    const band = page.getByRole('region', {
      name: 'Fault investigation context'
    });
    await readable(band.getByText(/Resolved does not always mean cleared/), 14);
    await readable(
      band.getByText(/Recorded occurrences and retained evidence/),
      14
    );
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
      open.getByRole('button', { name: 'Acknowledge', exact: true })
    ).toHaveCount(canChange ? 1 : 0);
    for (const row of [open, ack]) {
      await expect(
        row.getByRole('button', { name: 'Dismiss', exact: true })
      ).toHaveCount(canChange ? 1 : 0);
      await expect(
        row.getByRole('button', { name: 'Create repair', exact: true })
      ).toHaveCount(canAdd ? 1 : 0);
    }
    await expect(
      ack.getByRole('button', { name: 'Acknowledge', exact: true })
    ).toHaveCount(0);
    await expect(
      open.getByRole('link', { name: 'Open event and trend' })
    ).toBeVisible();
    if (canChange) {
      await open
        .getByRole('button', { name: 'Acknowledge', exact: true })
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
      await open.getByRole('button', { name: 'Dismiss', exact: true }).click();
      await expect(
        page
          .getByRole('dialog')
          .getByRole('button', { name: 'Dismiss', exact: true })
      ).toBeDisabled();
    }
  });
}

test('the theme lg boundary keeps the location tree and results in step', async ({
  page
}) => {
  await mockExplorer(page);
  await openExplorer(page, '/machines/index/sites/?location=12');
  for (const width of [1183, 1184, 1190, 1199, 1200]) {
    await page.setViewportSize({ width, height: 900 });
    const tree = page.locator('#machine-location-browser');
    const disclosure = page.getByRole('button', { name: 'Choose location' });
    if (width < 1184) {
      await expect(tree).toBeHidden();
      await expect(disclosure).toBeVisible();
    } else {
      await expect(tree).toBeVisible();
      await expect(disclosure).toBeHidden();
      const treeBox = (await tree.boundingBox())!;
      const summaryBox = (await page
        .getByRole('region', { name: 'Loaded page summary' })
        .boundingBox())!;
      expect(summaryBox.x).toBeGreaterThanOrEqual(
        treeBox.x + treeBox.width - 1
      );
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(width);
  }
});

test('supplied plant timezone is used for alarm observations', async ({
  page
}) => {
  await mockEquipment(page);
  await openEquipment(page, true, 'light', {
    plant_timezone: 'America/Chicago'
  });
  const row = page.locator('tr[data-anomaly="901"]');
  await expect(row).toContainText('(America/Chicago)');
  await expect(row).not.toContainText('(Asia/Riyadh)');
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
