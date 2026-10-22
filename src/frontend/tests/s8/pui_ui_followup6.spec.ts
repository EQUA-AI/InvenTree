import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { type Page, expect, test } from '@playwright/test';
import { SIGNALS, seriesFor, signalRows } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { components } from '../s2/partsFixtures';
import { NATIVE_SIGNALS, openPerformance } from '../s3/performanceFixtures';
import { contrast } from './contrast';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
const captures = resolve('../../data/devscratch/ui-r2/followup6/captures');
async function capture(page: Page, name: string) {
  await mkdir(captures, { recursive: true });
  await page.screenshot({
    path: resolve(captures, `${name}.png`),
    animations: 'disabled'
  });
}
async function paused(
  page: Page,
  options: Parameters<typeof openPerformance>[1]
) {
  const requests = await openPerformance(page, {
    ...options,
    settleInitialRead: true
  });
  const live = page.getByRole('switch', { name: /^Live updates:/ });
  await live.focus();
  if (await live.isChecked()) await live.press('Space');
  await expect(live).not.toBeChecked();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  return requests;
}
async function parts(
  page: Page,
  scheme: 'light' | 'dark',
  assetType: 'pump' | 'pumphouse' = 'pump'
) {
  await mockMachine(page, { assetType });
  await page.route('**/api/assets/registry/18/components/**', (route) =>
    route.fulfill({ json: { count: 3, results: components(true) } })
  );
  await page.route('**/api/assets/parts/**', (route) =>
    route.fulfill({ json: { count: 0, results: [], next: null } })
  );
  const query = new URLSearchParams({
    surface: 'machine-shell',
    entry: '/machines/machine/18/parts/',
    nativeTheme: 'true',
    compactChrome: 'true',
    scheme
  });
  await page.goto(`/playwright/s0-native.html?${query}`);
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await expect(
    page.getByRole('button', { name: 'Open work: Read failed', exact: true })
  ).toBeVisible();
}
for (const scheme of ['light', 'dark'] as const) {
  test(`native Parts failure phrase and numeric columns fit 1280 ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    for (const assetType of ['pump', 'pumphouse'] as const) {
      await parts(page, scheme, assetType);
      const failure = page.getByRole('button', {
        name: 'Open work: Read failed',
        exact: true
      });
      const label = failure.getByText('Read failed', { exact: true });
      expect(
        await label.evaluate((node) => {
          const range = document.createRange();
          range.selectNodeContents(node);
          return Array.from(range.getClientRects()).filter(
            (r) => r.width && r.height
          ).length;
        })
      ).toBe(1);
      const cell = failure.locator('xpath=ancestor::td');
      const box = (await cell.boundingBox())!;
      const badge = (await failure.boundingBox())!;
      expect(badge.x).toBeGreaterThanOrEqual(box.x - 1);
      expect(badge.x + badge.width).toBeLessThanOrEqual(box.x + box.width + 1);
      for (const [name, field] of [
        ['Open work', 'open_work'],
        ['In stock', 'stock']
      ]) {
        await expect(
          page
            .getByRole('columnheader', { name, exact: true })
            .getByText(name, { exact: true })
        ).toHaveCSS('text-align', 'right');
        await expect(
          page.locator(`tbody td:has([data-field="${field}"])`).first()
        ).toHaveCSS('text-align', 'right');
      }
      for (const header of await page.getByRole('columnheader').all()) {
        expect(
          await header.evaluate((cell) => {
            const bounds = cell.getBoundingClientRect();
            const walker = document.createTreeWalker(
              cell,
              NodeFilter.SHOW_TEXT
            );
            const overflow: string[] = [];
            for (let node = walker.nextNode(); node; node = walker.nextNode()) {
              if (!node.textContent?.trim()) continue;
              const range = document.createRange();
              range.selectNodeContents(node);
              for (const rect of range.getClientRects())
                if (
                  rect.width &&
                  rect.height &&
                  (rect.left < bounds.left - 1 ||
                    rect.right > bounds.right + 1 ||
                    rect.top < bounds.top - 1 ||
                    rect.bottom > bounds.bottom + 1)
                )
                  overflow.push(node.textContent);
            }
            return overflow;
          })
        ).toEqual([]);
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth)
      ).toBeLessThanOrEqual(1280);
      await capture(page, `parts-native-1280-${assetType}-${scheme}`);
    }
  });

  test(`phone comparison machine selection stays positioned ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    if (scheme === 'dark') await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.route('**/api/assets/machines/**', (route) =>
      route.fulfill({
        json: {
          count: 1,
          results: [{ pk: 19, name: 'Permitted pump', asset_type: 'pump' }]
        }
      })
    );
    await page.route(
      '**/api/machine-health/machines/19/health/signals/**',
      (route) => route.fulfill({ json: { results: signalRows(SIGNALS) } })
    );
    const requests: URL[] = [];
    await page.route(
      '**/api/machine-health/machines/19/health/series/**',
      (route) => {
        const url = new URL(route.request().url());
        requests.push(url);
        return route.fulfill({ json: { ...seriesFor(url), machine: 19 } });
      }
    );
    await paused(page, { harness: 's3', scheme });
    await page
      .getByRole('button', {
        name: 'Comparisons and saved views',
        exact: true
      })
      .click();
    await page
      .getByRole('combobox', { name: 'Comparison channels', exact: true })
      .fill('Shaft speed');
    await page
      .getByRole('option')
      .filter({ hasText: 'Shaft speed' })
      .first()
      .click();
    await page.keyboard.press('Escape');
    await page
      .getByRole('combobox', { name: 'Comparison mode', exact: true })
      .click();
    await page
      .getByRole('option', { name: 'Another permitted machine', exact: true })
      .click();
    const machine = page.getByRole('combobox', {
      name: 'Comparison machine',
      exact: true
    });
    await machine.click();
    const option = page.getByRole('option', {
      name: 'Permitted pump',
      exact: true
    });
    await expect(option).toBeVisible();
    await option.click();
    await expect(machine).toHaveValue('Permitted pump');
    await expect(machine).toBeInViewport();
    await expect.poll(() => requests.length).toBe(1);
    await expect(
      page.getByTestId('comparison-workbench').locator('[data-trend-sync]')
    ).toHaveCount(2);
    await capture(page, `comparison-machine-phone-${scheme}`);
    await machine.click();
    await expect(option).toBeVisible();
    await expect(machine).toBeInViewport();
    await capture(page, `comparison-machine-open-phone-${scheme}`);
  });
  test(`phone Parts hovered links retain contrast ${scheme}`, async ({
    page
  }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await parts(page, scheme);
    const component = page.getByRole('button', {
      name: 'Thrust Bearing',
      exact: true
    });
    await component.scrollIntoViewIfNeeded();
    await component.hover();
    const measured = await contrast(component);
    await info.attach('hovered-component-contrast', {
      body: JSON.stringify(measured),
      contentType: 'application/json'
    });
    expect(measured.ratio).toBeGreaterThanOrEqual(4.5);
    await mkdir(captures, { recursive: true });
    await writeFile(
      resolve(captures, `parts-hover-${scheme}.json`),
      JSON.stringify(measured, null, 2)
    );
    const catalogue = page.getByRole('link', {
      name: 'Thrust Bearing',
      exact: true
    });
    expect((await contrast(catalogue)).ratio).toBeGreaterThanOrEqual(4.5);
    await expect(catalogue).toHaveCSS(
      'color',
      await component.evaluate((node) => getComputedStyle(node).color)
    );
    await capture(page, `parts-hover-phone-${scheme}`);
  });
  for (const width of [390, 1440])
    test(`sensor threshold labels clear categories ${width} ${scheme}`, async ({
      page
    }) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
      await paused(page, {
        harness: 's3',
        scheme,
        signals: NATIVE_SIGNALS.filter((s) =>
          /WINDING_TEMPERATURED[1-3]$/.test(s.key)
        )
      });
      const comparison = page.getByTestId('sensor-comparison');
      const key = comparison.getByTestId('sensor-comparison-limits');
      await expect(key).toContainText('Warning above 125 °C');
      await expect(key).toContainText('Critical above 145 °C');
      await comparison.scrollIntoViewIfNeeded();
      const limits = (await key.boundingBox())!;
      const plot = (await comparison
        .locator('svg.recharts-surface')
        .boundingBox())!;
      expect(limits.y + limits.height).toBeLessThanOrEqual(plot.y);
      for (const name of ['Winding 1', 'Winding 2', 'Winding 3'])
        await expect(
          comparison.locator('svg text').filter({ hasText: name })
        ).toBeVisible();
      await expect(
        comparison.locator('.recharts-reference-line-line')
      ).toHaveCount(2);
      expect(
        await comparison.evaluate(
          (node) => node.scrollWidth <= node.clientWidth + 1
        )
      ).toBe(true);
      await capture(page, `sensor-limits-${width}-${scheme}`);
    });
}

for (const scheme of ['light', 'dark'] as const)
  test(`phone comparison editors stay reachable through ordinary selection ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const requests = await paused(page, {
      harness: 's3',
      scheme,
      signals: NATIVE_SIGNALS.filter((s) =>
        /PUMP1_SPEED$|WINDING_TEMPERATURED[12]$/.test(s.key)
      )
    });
    await page
      .getByRole('button', { name: 'Comparisons and saved views', exact: true })
      .click();
    const input = page.getByRole('combobox', {
      name: 'Comparison channels',
      exact: true
    });
    for (const name of ['Winding 1', 'Winding 2', 'Shaft speed']) {
      await input.fill(name);
      const option = page.getByRole('option').filter({ hasText: name }).first();
      await expect(option).toBeVisible();
      await option.click();
      await expect(input).toBeFocused();
      await expect(input).toBeInViewport();
      await page.keyboard.press('Escape');
    }
    await expect(page.locator('[data-comparison-group]')).toHaveCount(2);
    await page.getByLabel('View name', { exact: true }).fill('Investigation');
    await page.getByRole('button', { name: 'Save view', exact: true }).click();
    await expect(
      page.getByText('Display view saved in this browser.', { exact: true })
    ).toBeVisible();
    expect(requests).toHaveLength(1);
    await page.reload();
    await page
      .getByRole('button', { name: 'Comparisons and saved views', exact: true })
      .click();
    const saved = page.getByRole('combobox', {
      name: 'Saved view',
      exact: true
    });
    await saved.click();
    await expect(
      page.getByRole('option', { name: 'Investigation', exact: true })
    ).toBeVisible();
    await page
      .getByRole('option', { name: 'Investigation', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Restore view', exact: true })
      .click();
    await expect(page.locator('[data-comparison-group]')).toHaveCount(2);
    await page
      .getByRole('combobox', { name: 'Comparison mode', exact: true })
      .click();
    await page
      .getByRole('option', { name: 'Before / after windows', exact: true })
      .click();
    await expect(
      page.getByLabel('Window B from', { exact: true })
    ).toBeVisible();
    expect(
      await page
        .getByTestId('comparison-workbench')
        .evaluate((node) => node.scrollWidth <= node.clientWidth + 1)
    ).toBe(true);
    await capture(page, `comparison-phone-${scheme}`);
    const mode = page.getByRole('combobox', {
      name: 'Comparison mode',
      exact: true
    });
    await mode.click();
    await expect(
      page.getByRole('option', { name: 'Before / after windows', exact: true })
    ).toBeVisible();
    await expect(mode).toBeInViewport();
    await capture(page, `comparison-mode-open-phone-${scheme}`);
  });
