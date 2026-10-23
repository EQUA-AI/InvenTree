import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import { fixture, observed } from '../mimic/layoutFixtures';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { components } from '../s2/partsFixtures';
import { openHealth } from '../s3/healthFixtures';
import { readable } from './contrast';
import { captureFromTop, openExplorer } from './designFixtures';
import { mockOwnerLocations, ownerMimicFixture } from './ownerFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();

const views = [
  ['01-mimic-station-desktop-light', 'station', 'light', 1440],
  ['02-mimic-station-desktop-dark', 'station', 'dark', 1440],
  ['03-mimic-station-phone-light', 'station', 'light', 390],
  ['04-mimic-pump-desktop-light', 'pump', 'light', 1440],
  ['05-mimic-pump-desktop-dark', 'pump', 'dark', 1440],
  ['06-mimic-pump-phone-light', 'pump', 'light', 390],
  ['07-machines-light-desktop', 'machines', 'light', 1440],
  ['08-machines-dark-desktop', 'machines', 'dark', 1440],
  ['09-machines-light-phone', 'machines', 'light', 390],
  ['10-location-light-desktop', 'location', 'light', 1440],
  ['11-health-light-desktop', 'health', 'light', 1440],
  ['12-health-dark-desktop', 'health', 'dark', 1440],
  ['13-health-light-phone', 'health', 'light', 390],
  ['14-station-parts-light-desktop', 'station-parts', 'light', 1440],
  ['15-station-parts-light-phone', 'station-parts', 'light', 390],
  ['16-pump-parts-light-desktop', 'pump-parts', 'light', 1440],
  ['17-pump-parts-dark-desktop', 'pump-parts', 'dark', 1440],
  ['18-pump-parts-light-phone', 'pump-parts', 'light', 390],
  ['19-location-dark-desktop', 'location', 'dark', 1440],
  ['20-location-light-phone', 'location', 'light', 390],
  ['21-location-selected-light-desktop', 'location-selected', 'light', 1440],
  ['22-health-light-tablet-768x1024', 'health', 'light', 768],
  ['23-mimic-station-light-desktop-1366x768', 'station', 'light', 1366]
] as const;

for (const [name, screen, scheme, width] of views) {
  test(`${name} final-source capture`, async ({ page }, info) => {
    test.skip(
      info.project.name.endsWith('phone'),
      'Numbered matrix sets its own viewport'
    );
    await page.setViewportSize({
      width,
      height:
        width === 390 ? 844 : width === 768 ? 1024 : width === 1366 ? 768 : 900
    });
    if (screen === 'station' || screen === 'pump') {
      await page.clock.setFixedTime(new Date(observed));
      await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
        route.fulfill({
          json: (process.env.AIMMS_OWNER_CAPTURE === '1'
            ? ownerMimicFixture
            : fixture)(new URL(route.request().url()).searchParams.get('unit'))
        })
      );
      const query = new URLSearchParams({ scheme });
      if (screen === 'pump') query.set('pump', 'P7');
      await page.goto(`/tests/mimic/index.html?${query}`);
      await expect(
        page
          .locator(
            screen === 'station'
              ? '[data-station-metric]'
              : '[data-unit-drawing]'
          )
          .first()
      ).toBeVisible();
      if (screen === 'station') {
        await expect(
          page.locator('[data-station-metric], [data-total]')
        ).toHaveCount(5);
        await expect(page.locator('g[data-bay]')).toHaveCount(14);
        await expect(page.locator('[data-section="station"]')).toHaveCount(0);
      } else {
        await expect(
          page.locator('[data-callout-column] button[data-part]')
        ).toHaveCount(13);
        await expect(page.getByLabel('Process data')).toHaveCount(0);
        await expect(
          page.locator('[data-callout-column]').filter({ hasText: 'degC' })
        ).toHaveCount(0);
      }
    } else if (
      screen === 'machines' ||
      screen === 'location' ||
      screen === 'location-selected'
    ) {
      if (screen === 'location') {
        await mockOwnerLocations(page);
        await page.goto(
          `/playwright/s8-design.html?${new URLSearchParams({ screen: 'machines', entry: '/machines/index/sites/', scheme })}`
        );
        // Cold Vite module loading is fixture setup. Keep the initial phone
        // disclosure state and begin strict data checks once native inputs mount.
        await expect(
          page.getByRole('textbox', {
            name: 'Search machines',
            exact: true,
            includeHidden: true
          })
        ).toBeAttached({ timeout: 60_000 });
        await expect(page.locator('[data-machine]')).toHaveCount(25);
        await expect(page.getByTestId('machine-page-count')).toHaveText(
          '25 of 86'
        );
        await expect(
          page.locator('[data-machine="1"] [data-location-path]')
        ).toHaveAttribute(
          'data-location-path',
          /Cedar Creek Houston Regional Site.*Pump Bay 01/
        );
        await expect(
          page.getByRole('link', { name: /View unassigned machines/ })
        ).toHaveCount(0);
        if (width === 390) {
          await expect(
            page.getByRole('button', { name: 'Choose location', exact: true })
          ).toHaveAttribute('aria-expanded', 'false');
          const first = (await page
            .locator('[data-machine="1"]')
            .boundingBox())!;
          expect(
            first.y + first.height,
            'The complete root machine card is visible without choosing a site'
          ).toBeLessThanOrEqual(844);
        } else {
          const roots = page.getByRole('list', {
            name: 'Top-level locations',
            exact: true
          });
          await expect(roots.getByRole('listitem')).toHaveCount(7);
          await expect(
            roots
              .getByRole('listitem')
              .filter({ hasText: 'Cedar Creek Houston Regional Site' })
          ).toContainText('63 machines');
        }
      } else {
        const data = await mockExplorer(page);
        data.state.totalLimit = 8;
        data.state.canChange = true;
        data.location.counts.direct_machines = 8;
        data.location.counts.total_machines = 8;
        await openExplorer(
          page,
          screen === 'machines'
            ? '/machines/index/machines/'
            : '/machines/index/sites/?location=12',
          scheme
        );
        await expect(page.locator('[data-machine]')).toHaveCount(8);
        await expect(page.locator('output button')).toHaveCount(0);
        if (width !== 390)
          for (const header of await page.locator('thead th').all())
            if ((await header.textContent())?.trim()) await readable(header);
        if (width === 390) {
          const checkbox = page.getByRole('checkbox').first();
          const box = (await checkbox.boundingBox())!;
          expect(box.width).toBe(20);
          expect(box.height).toBe(20);
        }
      }
    } else if (screen === 'health') {
      await openHealth(page, {
        scheme,
        nativeTheme: true,
        compactChrome: true
      });
      await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
      await expect(
        page.getByText('0 active anomalies', { exact: true })
      ).toBeVisible();
    } else {
      const station = screen === 'station-parts';
      await mockMachine(page, { assetType: station ? 'pumphouse' : 'pump' });
      const rows = components(true).map((row) => ({
        ...row,
        machine_name: station ? 'Station' : 'Synthetic pump'
      }));
      await page.route('**/api/assets/registry/18/components/**', (route) =>
        route.fulfill({ json: { count: rows.length, results: rows } })
      );
      await page.route('**/api/assets/parts/?*', (route) =>
        route.fulfill({ json: { count: 0, results: [], next: null } })
      );
      await page.goto(
        `/playwright/s0-native.html?${new URLSearchParams({ surface: 'machine-shell', compactChrome: 'true', nativeTheme: 'true', scheme, entry: '/machines/machine/18/parts/' })}`
      );
      await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
      await expect(page.locator('[data-field="name"]')).toHaveCount(3);
    }
    await page.keyboard.press('Escape');
    await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
    await page.mouse.move(0, 0);
    await expect(page.getByRole('tooltip')).toHaveCount(0);
    await page.evaluate(() => document.fonts.ready);
    await captureFromTop(page);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(width);
    await expect(page.locator('[role="img"][tabindex="0"]')).toHaveCount(0);
    await expect(page.locator('output button')).toHaveCount(0);
    if (width === 390 && (screen === 'health' || screen.endsWith('parts'))) {
      const selected = page.getByRole('tab', {
        name: screen === 'health' ? 'Health' : 'Parts',
        exact: true
      });
      await expect(selected).toBeInViewport({ ratio: 1 });
      const firstRow = page.locator('table tbody tr').first();
      if (screen === 'health') {
        const value = (await firstRow.locator('td').nth(1).boundingBox())!;
        expect(
          value.y + value.height,
          'The actual reading is above the phone fold'
        ).toBeLessThanOrEqual(844);
      } else {
        const row = (await firstRow.boundingBox())!;
        expect(
          row.y + row.height,
          'A complete Parts card is above the phone fold'
        ).toBeLessThanOrEqual(844);
      }
    }
    const phase = process.env.AIMMS_R2_CAPTURE_PHASE ?? 'preview';
    expect(phase).toMatch(/^[a-z0-9-]+$/);
    const directory = resolve('../../data/devscratch/ui-r2', phase);
    await mkdir(directory, { recursive: true });
    const drawingGeometry =
      screen === 'pump'
        ? await page.locator('[data-unit-drawing]').evaluate((node) => {
            const drawing = node as SVGSVGElement;
            const native = drawing.querySelector('g > svg') as SVGSVGElement;
            const box = native.getBBox();
            const transform = native.getScreenCTM()!;
            const scale = Math.hypot(transform.a, transform.b);
            const bounds = drawing.getBoundingClientRect();
            return {
              contentWidth: drawing
                .closest('[data-unit-schematic]')!
                .getBoundingClientRect().width,
              width: bounds.width,
              height: bounds.height,
              paintedWidth: box.width * scale,
              paintedHeight: box.height * scale
            };
          })
        : undefined;
    const metadata = await page.evaluate(() => ({
      width: innerWidth,
      height: innerHeight,
      scrollY,
      scheme: document.documentElement.getAttribute(
        'data-mantine-color-scheme'
      ),
      visibleRows: [
        ...document.querySelectorAll('table tbody tr, [data-machine-card]')
      ].filter((node) => {
        const r = node.getBoundingClientRect();
        return (
          r.width > 0 &&
          r.height > 0 &&
          r.left >= 0 &&
          r.right <= innerWidth &&
          r.top >= 0 &&
          r.bottom <= innerHeight
        );
      }).length,
      activeTab: [
        ...document.querySelectorAll('[role="tab"][aria-selected="true"]')
      ].map((node) => {
        const r = node.getBoundingClientRect();
        return { text: node.textContent, left: r.left, right: r.right };
      })
    }));
    await page.screenshot({
      path: resolve(directory, `${name}.png`),
      animations: 'disabled'
    });
    if (screen === 'pump') {
      const fullDirectory = resolve(directory, 'full');
      await mkdir(fullDirectory, { recursive: true });
      await page.screenshot({
        path: resolve(fullDirectory, `${name}.png`),
        fullPage: true,
        animations: 'disabled'
      });
    }
    await writeFile(
      resolve(directory, `${name}.json`),
      JSON.stringify(
        {
          synthetic: true,
          renderer: 'Linux Chromium on owned WS3012',
          sourceRevision: process.env.AIMMS_SOURCE_REVISION,
          screen,
          capturedAt: new Date().toISOString(),
          drawingGeometry,
          ...metadata
        },
        null,
        2
      )
    );
  });
}
