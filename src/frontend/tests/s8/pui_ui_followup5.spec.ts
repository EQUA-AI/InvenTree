import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { type Page, expect, test } from '@playwright/test';
import { NOW } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { NATIVE_SIGNALS, openPerformance } from '../s3/performanceFixtures';
import { BAYS, STATION_SIGNALS, openStation } from '../s3/stationFixtures';
import { captureFromTop, openExplorer } from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
const directory = resolve('../../data/devscratch/ui-r2/final5');
const bays = Array.from({ length: 14 }, (_, index) => ({
  ...BAYS[0],
  key: `P${index + 1}`,
  machine: 18 + index,
  name: `Fixture station / Pump ${String(index + 1).padStart(2, '0')}`,
  state: index === 0 ? 'running' : 'idle',
  power: index === 0 ? 8.42 : 0,
  flow: index === 0 ? 812.5 : 0,
  speed: index === 0 ? 745 : 0,
  status: index === 0 ? 1 : 0
}));
const stationSignals = STATION_SIGNALS.map((signal) => ({
  ...signal,
  value: signal.key === '/pc' ? 1 : signal.value
}));
async function capture(page: Page, name: string) {
  await mkdir(directory, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await captureFromTop(page);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({
    path: resolve(directory, `${name}.png`),
    animations: 'disabled'
  });
}
async function compactCards(page: Page, phone: boolean) {
  const strip = page.getByTestId('kpi-strip');
  const cards = strip.locator('[data-kpi]');
  await expect(cards).toHaveCount(4);
  const available = await strip.evaluate(
    (node) => node.parentElement!.getBoundingClientRect().width
  );
  const box = (await strip.boundingBox())!;
  expect(box.width).toBeGreaterThanOrEqual(available - 2);
  for (const card of await cards.all()) {
    const rect = (await card.boundingBox())!;
    expect(rect.height, 'KPI cards size to content').toBeLessThan(190);
    expect(rect.width).toBeGreaterThanOrEqual(220);
    const observed = card.locator('[data-kpi-observed] time');
    if (await observed.count()) {
      expect(
        await observed.evaluate((node) => node.getBoundingClientRect().height)
      ).toBeLessThan(24);
      await expect(observed).not.toContainText(/server flag|budget/);
    }
  }
  const rects = await cards.evaluateAll((nodes) =>
    nodes.map((node) => {
      const r = node.getBoundingClientRect();
      return { x: r.x, y: r.y, width: r.width };
    })
  );
  if (!phone) {
    expect(new Set(rects.map((r) => Math.round(r.y))).size).toBe(1);
    expect(rects.at(-1)!.x + rects.at(-1)!.width).toBeGreaterThan(
      box.x + box.width - 2
    );
  } else expect(new Set(rects.map((r) => Math.round(r.x))).size).toBe(1);
  await expect(strip).not.toContainText(
    /Last known|unit not recorded|Window |no limit configured/
  );
}
for (const scheme of ['light', 'dark'] as const)
  test(`station compact full-width KPIs ${scheme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openStation(page, { bays, stationSignals, scheme });
    await compactCards(page, false);
    await expect(
      page.locator('[data-kpi="pumps-running"] [data-kpi-value]')
    ).toHaveText('1 of 14');
    await expect(
      page.getByRole('switch', { name: 'Live updates: Live', exact: true })
    ).toBeChecked();
    await expect(page.getByText('Live', { exact: true })).toHaveCount(1);
    await capture(
      page,
      scheme === 'light'
        ? '33-station-performance-desktop-light'
        : '34-station-performance-desktop-dark'
    );
    const liveSwitch = page.getByRole('switch', {
      name: 'Live updates: Live',
      exact: true
    });
    await liveSwitch.focus();
    await liveSwitch.press('Space');
    await expect(
      page.getByRole('switch', { name: 'Live updates: Paused', exact: true })
    ).not.toBeChecked();
    await expect(page.getByText('Live', { exact: true })).toHaveCount(0);
  });
test('station phone uses compact full-width cards', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openStation(page, { bays, stationSignals });
  await compactCards(page, true);
  await capture(page, '32-station-performance-phone-light');
});
test('stale KPIs keep a short badge and plain relative age', async ({
  page
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openStation(page, {
    bays,
    stationSignals: stationSignals.map((signal) => ({
      ...signal,
      stale: true,
      observedAt: new Date(NOW + 5000).toISOString()
    }))
  });
  await compactCards(page, false);
  const count = page.locator('[data-kpi="pumps-running"]');
  await expect(count.locator('[data-kpi-value]')).toHaveText('1 of 14');
  const badge = count.getByRole('button', { name: 'Stale', exact: true });
  await page.keyboard.press('Tab');
  await badge.focus();
  await expect(badge).toBeFocused();
  expect(
    (await badge.locator('.mantine-Badge-root').boundingBox())!.height
  ).toBeLessThan(32);
  const staleReason = page.getByRole('tooltip', {
    name: /Source flagged stale/
  });
  await expect(staleReason).toBeVisible();
  await expect(staleReason).toContainText('Observed 18 Oct 2026 12:00:05');
  await expect(staleReason).toContainText('Read 18 Oct 2026 12:00:00');
  await expect(staleReason).toContainText('freshness budget 300 s');
  await expect(badge).toHaveAccessibleDescription(
    await staleReason.innerText()
  );
  await expect(count.locator('.mantine-Badge-root time')).toHaveCount(0);
  const observed = count.locator('[data-kpi-observed] time');
  await expect(observed).toHaveText(
    /^\d{2}:\d{2}:\d{2} · 5 s ahead of your clock$/
  );
  await expect(observed).toHaveAttribute(
    'datetime',
    new Date(NOW + 5000).toISOString()
  );
  await page.keyboard.press('Escape');
  await expect(staleReason).toBeHidden();
  await capture(page, '35-station-performance-stale-desktop-light');
});
test('pump shares compact KPI cards and a single live control', async ({
  page
}) => {
  await openPerformance(page, {
    nativeTheme: true,
    compactChrome: true,
    signals: NATIVE_SIGNALS
  });
  for (const card of await page
    .getByTestId('kpi-strip')
    .locator('[data-kpi]')
    .all()) {
    expect((await card.boundingBox())!.height).toBeLessThan(190);
    expect((await card.boundingBox())!.width).toBeGreaterThanOrEqual(220);
  }
  await expect(
    page.getByRole('switch', { name: 'Live updates: Live', exact: true })
  ).toBeChecked();
  await expect(page.getByText('Live', { exact: true })).toHaveCount(1);
});
for (const view of ['machines', 'sites'] as const)
  test(`active scope and counts ${view}`, async ({ page }) => {
    const fixture = await mockExplorer(page);
    const details: URL[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (url.pathname === '/api/assets/locations/12/') details.push(url);
    });
    await openExplorer(
      page,
      view === 'machines'
        ? '/machines/index/machines/'
        : '/machines/index/sites/?location=12'
    );
    await expect
      .poll(() => fixture.requests.at(-1)?.params.get('active'))
      .toBe('true');
    await expect(
      page.getByText('Inactive record', { exact: true })
    ).toHaveCount(0);
    if (view === 'sites') {
      await expect
        .poll(() => details.at(-1)?.searchParams.get('active'))
        .toBe('true');
      await expect(
        page.getByRole('region', { name: 'Location totals' })
      ).toContainText(/79\s*machines/);
    } else
      await expect(page.getByText('25 of 109', { exact: true })).toBeVisible();
    const filters = page.getByRole('button', { name: 'Filters', exact: true });
    if (await filters.isVisible()) {
      await expect(filters).toHaveAttribute('aria-expanded', 'false');
      await filters.click();
      await expect(filters).toHaveAttribute('aria-expanded', 'true');
    }
    await page
      .getByRole('checkbox', { name: 'Show inactive', exact: true })
      .check();
    await expect
      .poll(() => fixture.requests.at(-1)?.params.has('active'))
      .toBe(false);
    await expect(
      page.getByText('Inactive record', { exact: true })
    ).toBeVisible();
    if (view === 'sites') {
      await expect
        .poll(() => details.at(-1)?.searchParams.has('active'))
        .toBe(false);
      await expect(
        page.getByRole('region', { name: 'Location totals' })
      ).toContainText(/80\s*machines/);
    } else
      await expect(page.getByText('25 of 110', { exact: true })).toBeVisible();
    await page
      .getByRole('checkbox', { name: 'Show inactive', exact: true })
      .uncheck();
    await expect
      .poll(() => fixture.requests.at(-1)?.params.get('active'))
      .toBe('true');
    await expect(
      page.getByText('Inactive record', { exact: true })
    ).toHaveCount(0);
  });
for (const width of [1440, 390])
  test(`archived roots and descendants require explicit scope at ${width}`, async ({
    page
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await page.clock.setFixedTime(new Date(NOW));
    const fixture = await mockExplorer(page);
    const archived = {
      ...fixture.location,
      pk: 90,
      name: 'Archived site',
      code: 'ARCHIVE',
      archived: true,
      path: [{ pk: 90, name: 'Archived site' }]
    };
    const child = {
      ...fixture.child,
      pk: 91,
      parent: 90,
      name: 'Archived descendant',
      code: 'ARCHIVE-CHILD',
      archived: false,
      path: [...archived.path, { pk: 91, name: 'Archived descendant' }]
    };
    const requests: URL[] = [];
    await page.route('**/api/assets/locations/?*', (route) => {
      const url = new URL(route.request().url());
      requests.push(url);
      const active = url.searchParams.get('active_only') === 'true';
      const rows = url.searchParams.has('search')
        ? active
          ? []
          : [child]
        : url.searchParams.get('parent') === '90'
          ? active
            ? []
            : [child]
          : active
            ? [fixture.location]
            : [fixture.location, archived];
      return route.fulfill({
        json: { count: rows.length, results: rows, next: null }
      });
    });
    await page.goto(
      '/playwright/s8-design.html?screen=machines&entry=/machines/index/sites/&scheme=light'
    );
    if (width === 390)
      await page
        .getByRole('button', { name: 'Choose location', exact: true })
        .click();
    await expect(
      page
        .getByRole('list', { name: 'Top-level locations', exact: true })
        .getByRole('listitem')
    ).toHaveCount(1);
    expect(
      requests.every((url) => url.searchParams.get('active_only') === 'true')
    ).toBe(true);
    await page
      .getByRole('textbox', { name: 'Search locations', exact: true })
      .fill('Archived');
    await expect
      .poll(() =>
        requests.some(
          (url) =>
            url.searchParams.get('search') === 'Archived' &&
            url.searchParams.get('active_only') === 'true'
        )
      )
      .toBe(true);
    await expect(
      page.getByRole('button', { name: /Archived descendant$/ })
    ).toHaveCount(0);
    await page
      .getByRole('checkbox', { name: 'Show archived locations', exact: true })
      .check();
    await expect
      .poll(() =>
        requests.some(
          (url) =>
            url.searchParams.get('search') === 'Archived' &&
            url.searchParams.get('active_only') === 'false'
        )
      )
      .toBe(true);
    await expect(
      page.getByRole('button', { name: /Archived descendant$/ })
    ).toBeVisible();
    await page
      .getByRole('textbox', { name: 'Search locations', exact: true })
      .clear();
    await expect(
      page
        .getByRole('list', { name: 'Top-level locations', exact: true })
        .getByRole('listitem')
    ).toHaveCount(2);
    const archivedRow =
      width === 390
        ? page
            .getByRole('list', { name: 'Location drill-down' })
            .getByRole('button', { name: 'Archived site', exact: true })
        : page
            .getByRole('tree')
            .getByRole('treeitem', { name: /^Archived site/ });
    await expect(archivedRow).toBeVisible();
    await page
      .getByRole('checkbox', { name: 'Show archived locations', exact: true })
      .uncheck();
    await expect(archivedRow).toHaveCount(0);
    await expect(
      page
        .getByRole('list', { name: 'Top-level locations', exact: true })
        .getByRole('listitem')
    ).toHaveCount(1);
  });

test('blank identity facts remain quiet dashes with keyboard reasons', async ({
  page
}) => {
  await mockMachine(page);
  await page.route('**/api/assets/machines/18/', (route) =>
    route.fulfill({
      json: {
        pk: 18,
        name: 'Pump 07',
        asset_type: 'pump',
        active: true,
        manufacturer: '',
        model: '',
        serial: '',
        description: 'Final effluent duty',
        physical_location: null,
        plant_timezone: 'Asia/Riyadh'
      }
    })
  );
  await page.goto(
    '/playwright/s8-design.html?screen=machine&entry=/machines/machine/18/details/&scheme=light'
  );
  for (const reason of [
    'Manufacturer not recorded',
    'Model not recorded',
    'Serial not recorded'
  ]) {
    const fact = page.getByRole('button', { name: reason, exact: true });
    await expect(fact).toHaveText('—');
    await page.keyboard.press('Tab');
    await fact.focus();
    await expect(
      page.getByRole('tooltip', { name: reason, exact: true })
    ).toBeVisible();
  }
});

test('a failed history read remains visible on the single live switch', async ({
  page
}) => {
  await openPerformance(page, {
    nativeTheme: true,
    compactChrome: true,
    failSeries: () => true
  });
  await expect(
    page.getByRole('switch', { name: 'Live updates: Read failed', exact: true })
  ).toBeChecked();
  await expect(page.getByText('Live', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Read failed', { exact: true })).toBeVisible();
});
