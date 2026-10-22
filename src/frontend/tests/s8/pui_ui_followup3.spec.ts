import { type Page, expect, test } from '@playwright/test';
import { observed } from '../mimic/layoutFixtures';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { components } from '../s2/partsFixtures';
import { openHealth, signalTable } from '../s3/healthFixtures';
import { readable } from './contrast';
import { captureFromTop, openExplorer } from './designFixtures';
import { mockOwnerLocations, ownerMimicFixture } from './ownerFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
async function mimic(
  page: Page,
  scheme: string,
  unit: boolean,
  acknowledged = () => false
) {
  await page.clock.setFixedTime(new Date(observed));
  await page.route('**/api/machine-health/station/17/mimic/**', (route) => {
    const data = ownerMimicFixture(
      new URL(route.request().url()).searchParams.get('unit')
    );
    if (acknowledged())
      for (const alarm of data.alarms) alarm.anomaly_status = 'acknowledged';
    return route.fulfill({ json: data });
  });
  await page.goto(
    `/tests/mimic/index.html?${new URLSearchParams({ scheme, ...(unit ? { pump: 'P7' } : {}) })}`
  );
  await expect(
    page.locator(unit ? '[data-unit-drawing]' : '[data-station-metrics]')
  ).toBeVisible();
}
for (const scheme of ['light', 'dark'] as const) {
  for (const width of [1366, 390]) {
    test(`follow-up 3 station fit and P9 reveal ${scheme} ${width}`, async ({
      page
    }) => {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 768 });
      await mimic(page, scheme, false);
      await expect(
        page.locator(
          '[data-station-metric="pumps-running"] [data-reading-value]'
        )
      ).toHaveText('2 of 14');
      await expect(page.locator('[data-legend]')).toHaveCount(7);
      await expect(
        page.getByText('Swipe to view all pump bays', { exact: true })
      ).toHaveCount(0);
      for (const total of await page.locator('[data-total]').all())
        await expect(total).not.toContainText('Σ');
      if (width > 390) {
        for (const size of [1366, 1280, 1024]) {
          await page.setViewportSize({ width: size, height: 900 });
          await expect
            .poll(async () =>
              page.locator('[data-bay="P14"]').evaluate((node) => {
                const a = node.getBoundingClientRect();
                const b = node.closest('section')!.getBoundingClientRect();
                return a.left >= b.left && a.right <= b.right;
              })
            )
            .toBe(true);
        }
      }
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.locator('[data-bay="P9"]').click();
      const heading = page.getByRole('heading', {
        name: 'Synthetic pump 9',
        exact: true
      });
      await expect(heading).toBeFocused();
      await expect(heading).toBeInViewport({ ratio: 1 });
      await expect(heading).toHaveAttribute('tabindex', '-1');
      expect(
        await heading.evaluate(
          (node) =>
            !!(
              node.compareDocumentPosition(
                [...document.querySelectorAll('h4')].find(
                  (h) => h.textContent === 'Threshold alarms'
                )!
              ) & Node.DOCUMENT_POSITION_FOLLOWING
            )
        )
      ).toBe(true);
    });
  }
  for (const screen of ['station', 'pump']) {
    test(`follow-up 3 static acknowledgement and animation pause ${scheme} ${screen}`, async ({
      page
    }) => {
      let acknowledged = false;
      await mimic(page, scheme, screen === 'pump', () => acknowledged);
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      const pause = page.getByRole('button', {
        name: 'Pause animations',
        exact: true
      });
      await expect(pause).toHaveCount(1);
      const colors = await page.evaluate((unit) => {
        const value = document.querySelector(
          unit
            ? '[data-callout-column] [data-verdict="warning"] [data-part-summary]'
            : 'td[data-severity="warning"]'
        )!;
        const marker = document.querySelector(
          unit
            ? '[data-part-markers] [data-verdict="warning"] [data-number-surface]'
            : '[data-alarm-marker="warning"] polygon'
        )!;
        const badge = document.querySelector(
          '[data-attention][data-severity="warning"] .mantine-Badge-root'
        )!;
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 1;
        const context = canvas.getContext('2d')!;
        const rgba = (color: string) => {
          context.clearRect(0, 0, 1, 1);
          context.fillStyle = color;
          context.fillRect(0, 0, 1, 1);
          return [...context.getImageData(0, 0, 1, 1).data];
        };
        return {
          value: rgba(getComputedStyle(value).color),
          marker: rgba(getComputedStyle(marker).fill),
          badge: rgba(getComputedStyle(badge).backgroundColor)
        };
      }, screen === 'pump');
      expect(colors.value).toEqual(colors.marker);
      expect(colors.badge).toEqual(colors.marker);

      await expect(pause).toHaveAttribute('aria-pressed', 'false');
      const flow = page.locator('[data-discharge-flow]').first();
      await expect(flow).toHaveCSS('animation-play-state', 'running');
      await expect(
        page.locator('[data-unacknowledged-ring]').first()
      ).toBeVisible();
      await pause.click();
      await expect(pause).toHaveAttribute('aria-pressed', 'true');
      await expect(flow).toHaveCSS('animation-play-state', 'paused');
      await expect(
        page.locator('[data-unacknowledged-ring]').first()
      ).toBeVisible();
      await expect(
        page.locator('[data-attention="unacknowledged"]').first()
      ).toHaveCSS('opacity', '1');
      acknowledged = true;
      await page
        .getByRole('button', { name: 'Refresh', exact: true })
        .first()
        .click();
      await expect(page.locator('[data-unacknowledged-ring]')).toHaveCount(0);
      await expect(
        page.locator('[data-attention="acknowledged"]').first()
      ).toBeVisible();
      await pause.click();
      await expect(flow).toHaveCSS('animation-play-state', 'running');
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await expect(pause).toHaveAttribute('aria-pressed', 'true');
      await expect(flow).toHaveCSS('animation-name', 'none');
      if (screen === 'pump') {
        await readable(
          page.locator(
            '[data-callout-column] [data-verdict="warning"] [data-part-summary]'
          )
        );
        await readable(
          page.locator('[data-part-markers] [data-verdict="warning"] text')
        );
      }
    });
  }
  test(`follow-up 3 Health boundary controls and compact coverage ${scheme}`, async ({
    page
  }) => {
    for (const width of [767, 768, 769]) {
      await page.setViewportSize({ width, height: 1024 });
      await openHealth(page, {
        scheme,
        nativeTheme: true,
        compactChrome: true
      });
      const refresh = page
        .getByTestId('pump-health')
        .getByRole('button', { name: 'Refresh', exact: true });
      await expect(refresh).toHaveCount(1);
      await expect(refresh).toBeVisible();
      if (width === 767)
        await page
          .getByRole('button', { name: 'Signal filters', exact: true })
          .click();
      await expect(
        page.getByRole('combobox', { name: 'Component filter', exact: true })
      ).toBeVisible();
      await expect(
        page.getByRole('combobox', { name: 'Component filter', exact: true })
      ).toHaveAttribute('placeholder', 'All components');
      await expect(
        page.getByRole('combobox', { name: 'Order by', exact: true })
      ).toBeVisible();
      await expect(
        page.locator('label').filter({ hasText: 'Search signals' })
      ).toHaveCount(0);
      await expect(
        page.getByText('Critical signals: not designated', { exact: true })
      ).toHaveCount(0);
      const coverage = page
        .getByTestId('health-summary')
        .getByRole('button', { name: /Normal.*Assessed on 2 of 6/ });
      await page.keyboard.press('Tab');
      await coverage.focus();
      await expect(
        page
          .getByRole('tooltip')
          .filter({ hasText: 'No critical-signal designation is supplied' })
      ).toBeVisible();
      await page.keyboard.press('Escape');
      const firstTime = signalTable(page).locator('tbody tr time').first();
      await expect(firstTime).toHaveText('just now');
      await expect(firstTime).toHaveCSS('white-space', 'nowrap');
      if (width >= 768) {
        const titles = await Promise.all(
          ['Current condition', 'Signals'].map((name) =>
            page
              .getByRole('heading', { name, exact: true })
              .evaluate((node) => getComputedStyle(node).fontSize)
          )
        );
        expect(titles[0]).toBe(titles[1]);
      }
    }
  });
  test(`follow-up 3 Machines selection and opt-in tabs ${scheme}`, async ({
    page
  }) => {
    const explorer = await mockExplorer(page);
    explorer.state.canChange = true;
    await openExplorer(page, '/machines/index/machines/', scheme);
    await expect(
      page.getByRole('heading', { name: 'All Machines', exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Move selected', exact: true })
    ).toHaveCount(0);
    await expect(
      page
        .locator('label')
        .filter({ hasText: /^Search machines$|^Criticality$/ })
    ).toHaveCount(0);
    const selection = page.getByRole('checkbox', {
      name: 'Select Synthetic pump 001',
      exact: true
    });
    await selection.check();
    await expect(selection).toBeChecked();
    const filters = page.getByRole('button', { name: 'Filters', exact: true });
    if (await filters.isVisible()) {
      await expect(filters).toHaveAttribute('aria-expanded', 'false');
      await filters.click();
      await expect(filters).toHaveAttribute('aria-expanded', 'true');
    }
    await expect(
      page.getByRole('button', { name: /Move selected/ })
    ).toBeVisible();
    const alarm = page.getByRole('button', { name: /In alarm ·/ });
    await alarm.click();
    await expect(alarm).toHaveAttribute('aria-pressed', 'true');
    await expect(alarm).toHaveCSS('outline-style', 'solid');
    await page.setViewportSize({ width: 390, height: 844 });
    const tabs = page.getByRole('tablist', {
      name: 'panel-tabs-asset-machine-index',
      exact: true
    });
    await expect(tabs).toHaveCSS('mask-image', 'none');
    const panel = page.locator(
      '[aria-label="nav-panel-asset-machine-index-machines"]'
    );
    await expect(panel).not.toHaveCSS('padding-top', '0px');
  });
  test(`follow-up 3 phone location reasons dismiss after drill-down ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const nodes = await mockOwnerLocations(page);
    await page.goto(
      `/playwright/s8-design.html?${new URLSearchParams({ screen: 'machines', entry: '/machines/index/sites/', scheme })}`
    );
    await expect(
      page.getByText('Browse top-level locations', { exact: true })
    ).toHaveCount(1);
    await page
      .getByRole('button', { name: 'Choose location', exact: true })
      .click();
    for (const pk of [12, 20, 30])
      await page
        .getByRole('list', { name: 'Location drill-down' })
        .getByRole('button', { name: nodes.get(pk)!.name, exact: true })
        .click();
    const row = page.getByRole('button', {
      name: 'Discharge Gallery',
      exact: true
    });
    await row.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      'cc-feps-discharge-gallery'
    );
    await row.press('Escape');
    await expect(page.getByRole('tooltip')).toHaveCount(0);
    await row.blur();
    await page.mouse.move(0, 0);
    await expect(page.getByRole('tooltip')).toHaveCount(0);
  });
  test(`follow-up 3 Parts single-page footer and inline badge ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockMachine(page, { assetType: 'pump' });
    let total = 3;
    const rows = components(true);
    const many = Array.from({ length: 30 }, (_, i) => ({
      ...rows[i % 3],
      pk: i + 1
    }));
    await page.route('**/api/assets/registry/18/components/**', (route) => {
      const params = new URL(route.request().url()).searchParams;
      const limit = Number(params.get('limit') ?? 25);
      const offset = Number(params.get('offset') ?? 0);
      return route.fulfill({
        json: {
          count: total,
          results: (total === 3 ? rows : many).slice(offset, offset + limit)
        }
      });
    });
    await page.route('**/api/assets/parts/?*', (route) =>
      route.fulfill({ json: { count: 0, results: [], next: null } })
    );
    await page.goto(
      `/playwright/s0-native.html?${new URLSearchParams({ surface: 'machine-shell', compactChrome: 'true', nativeTheme: 'true', scheme, entry: '/machines/machine/18/parts/' })}`
    );
    await expect(page.locator('[data-field="name"]')).toHaveCount(3);
    await expect(
      page.getByText('Records per page', { exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByRole('columnheader', { name: 'In stock', exact: true })
    ).toHaveCount(1);
    const badge = page
      .locator('[data-field="status"] button .mantine-Badge-root')
      .first();
    expect(await badge.evaluate((node) => node.tagName)).toBe('SPAN');
    await captureFromTop(page);
    const first = (await page.locator('tbody tr').first().boundingBox())!;
    expect(first.y + first.height).toBeLessThanOrEqual(844);
    total = 30;
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(
      page.getByText('Records per page', { exact: true })
    ).toBeVisible();
  });
}
