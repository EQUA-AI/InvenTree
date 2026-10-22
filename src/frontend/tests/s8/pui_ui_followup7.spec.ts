import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { type Page, expect, test } from '@playwright/test';
import { NOW } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import { mockPositions } from '../s2/equipmentFixtures';
import { components } from '../s2/partsFixtures';
import { HEALTH_SIGNALS, openHealth } from '../s3/healthFixtures';
import { mockPerformance } from '../s3/performanceFixtures';
import { BAYS, STATION_SIGNALS, openStation } from '../s3/stationFixtures';
import { readable, svgEdge } from './contrast';
import { captureFromTop, mockEquipment } from './designFixtures';
import { ownerMimicFixture } from './ownerFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
for (const scheme of ['light', 'dark'])
  test(`normal reading marker keeps colour and readable numeral ${scheme}`, async ({
    page
  }) => {
    await page.clock.setFixedTime(new Date('2026-10-22T06:32:41Z'));
    await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
      route.fulfill({ json: ownerMimicFixture('P7') })
    );
    await page.goto(
      `/tests/mimic/index.html?${new URLSearchParams({ pump: 'P7', scheme })}`
    );
    const marker = page.locator('[data-part-markers] [data-verdict="normal"]');
    await expect(marker).toHaveCount(1);
    await expect(marker.locator('[data-normal-check]')).toBeVisible();
    await readable(marker.locator('text'));
    await svgEdge(marker.locator('[data-number-surface]'));
  });
const directory = resolve('../../data/devscratch/ui-r2/final7');
async function capture(page: Page, name: string) {
  await mkdir(directory, { recursive: true });
  await page.keyboard.press('Escape');
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
  await page.mouse.move(0, 0);
  await expect(page.getByRole('tooltip')).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
  if (
    (await page.evaluate(() => document.documentElement.scrollWidth)) >
    page.viewportSize()!.width
  )
    await writeFile(
      resolve(directory, `${name}-overflow.json`),
      JSON.stringify(
        await page.evaluate(() =>
          Array.from(document.querySelectorAll('body *'))
            .filter((node) => node.getBoundingClientRect().right > innerWidth)
            .map((node) => ({
              tag: node.tagName,
              class: node.getAttribute('class'),
              text: node.textContent?.slice(0, 120),
              bounds: node.getBoundingClientRect().toJSON(),
              position: getComputedStyle(node).position,
              overflow: getComputedStyle(node.parentElement!).overflowX
            }))
        ),
        null,
        2
      )
    );
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({
    path: resolve(directory, `${name}.png`),
    animations: 'disabled'
  });
}
async function machineTab(page: Page, tab: string, scheme = 'light') {
  const entry = `/machines/machine/18/${tab ? `${tab}/` : ''}`;
  await page.goto(
    `/playwright/s8-design.html?${new URLSearchParams({ screen: 'machine', entry, scheme })}`
  );
  await expect(
    page.getByText('Loading native panel…', { exact: true })
  ).toHaveCount(0);
  return entry;
}
for (const scheme of ['light', 'dark'] as const) {
  for (const width of [1440, 1280])
    test(`equipment default uses container-sized schematic ${width} ${scheme}`, async ({
      page
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.clock.setFixedTime(new Date('2026-10-22T06:32:41Z'));
      await mockEquipment(page);
      const data = ownerMimicFixture('P1');
      await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
        route.fulfill({ json: data })
      );
      const entry = await machineTab(page, 'equipment', scheme);
      await expect(
        page.locator('[data-equipment-view="schematic"]')
      ).toBeVisible();
      expect(new URL(page.url()).searchParams.get('entry')).toBe(entry);
      expect(entry).not.toContain('view=');
      const drawing = page.locator('[data-unit-drawing]');
      await expect(drawing).toBeVisible();
      await expect(
        page.locator('[data-callout-column] button[data-part]')
      ).toHaveCount(13);
      const host = await drawing.evaluate((node) => {
        let element: HTMLElement | null = node as HTMLElement;
        while (
          element &&
          getComputedStyle(element).containerType !== 'inline-size'
        )
          element = element.parentElement;
        return element?.getBoundingClientRect().width ?? 0;
      });
      const diagram = (await drawing.boundingBox())!;
      const callouts = await page
        .locator('[data-callout-column]')
        .evaluateAll((nodes) =>
          nodes.map((n) => n.getBoundingClientRect().toJSON())
        );
      expect(host).toBeGreaterThan(760);
      expect(diagram.width).toBeGreaterThan(host * 0.5);
      if (host < 1100)
        for (const callout of callouts)
          expect(callout.y).toBeGreaterThanOrEqual(
            diagram.y + diagram.height - 1
          );
      for (const card of await page
        .locator('[data-callout-column] button[data-part]')
        .all()) {
        expect(
          await card.evaluate(
            (node) => node.scrollWidth <= node.clientWidth + 1
          )
        ).toBe(true);
        if (host >= 1100) {
          const bounds = (await card.boundingBox())!;
          expect(bounds.y + bounds.height).toBeLessThanOrEqual(900);
        }
      }
      const name =
        width === 1440 && scheme === 'light'
          ? '30-equipment-default-desktop-light'
          : `equipment-default-${width}-${scheme}`;
      await capture(page, name);
      await writeFile(
        resolve(directory, `${name}.json`),
        JSON.stringify(
          {
            requestedEntry: entry,
            harnessUrl: page.url(),
            view: 'schematic',
            hostWidth: host,
            drawing: diagram
          },
          null,
          2
        )
      );
    });
  test(`Performance caps grid and uses honest ages and deltas ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.clock.setFixedTime(new Date(NOW));
    await mockMachine(page, { parent: 17, sourceKey: 'P1' });
    await mockPerformance(page);
    await machineTab(page, 'performance', scheme);
    const cards = page.getByTestId('kpi-strip').locator('[data-kpi]');
    await expect(cards).toHaveCount(8);
    const rows = await cards.evaluateAll((nodes) =>
      nodes.map((node) => {
        const r = node.getBoundingClientRect();
        return { top: Math.round(r.top), height: r.height };
      })
    );
    const groups = Map.groupBy(rows, (row) => row.top);
    expect(groups.size).toBe(2);
    for (const group of groups.values()) {
      expect(group).toHaveLength(4);
      expect(
        Math.max(...group.map((r) => r.height)) -
          Math.min(...group.map((r) => r.height))
      ).toBeLessThan(2);
    }
    await expect(page.locator('[data-kpi="speed"] time')).toHaveText(
      /20 s ago/
    );
    await expect(page.locator('[data-kpi-delta]').first()).toHaveText(
      /^Δ [+-]?\d+(\.\d+)? % \/ [\d.]+ (min|h)$/
    );
    const exceptions = page.getByRole('button', { name: /with poor quality/ });
    await exceptions.click();
    await expect(exceptions).toHaveAttribute('aria-pressed', 'true');
    await exceptions.click();
    await captureFromTop(page);
    await capture(page, `26-performance-desktop-${scheme}`);
    const heatmap = page.locator('[data-heatmap-cell]');
    await expect(heatmap.first()).toBeVisible();
    // State colours remain tied to configured limits even over the narrow healthy spread.
    const states = await heatmap.evaluateAll((nodes) =>
      nodes.map((node) => ({
        state: node.getAttribute('data-limit-state'),
        fill: node.getAttribute('fill')
      }))
    );
    const normalFills = states
      .filter((s) => s.state === 'normal')
      .map((s) => s.fill);
    expect(normalFills.length).toBeGreaterThan(0);
    expect(new Set(normalFills).size).toBe(1);
    await page
      .getByText('Current reading by sensor', { exact: true })
      .first()
      .scrollIntoViewIfNeeded();
    await capture(page, `31-performance-sensors-desktop-${scheme}`);
  });
}
const bays = Array.from({ length: 14 }, (_, i) => ({
  ...BAYS[0],
  key: `P${i + 1}`,
  machine: 18 + i,
  name: `Pump ${i + 1}`,
  state: i === 0 ? 'running' : 'idle',
  status: i === 0 ? 1 : 0,
  power: i === 0 ? 8.42 : 0,
  flow: i === 0 ? 812.5 : 0,
  speed: i === 0 ? 745 : 0
}));
test('station current readings keep their own source alongside window history', async ({
  page
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openStation(page, {
    bays,
    stationSignals: STATION_SIGNALS.map((signal) =>
      signal.key === '/pc' ? { ...signal, value: 1 } : signal
    )
  });
  const running = page.locator('[data-kpi="pumps-running"]');
  await expect(running).toContainText('1 of 14');
  await expect(
    page.getByRole('switch', { name: /^Live updates: Live/ })
  ).toBeChecked();
  await captureFromTop(page);
  await capture(page, '33-station-performance-desktop-light');
});
for (const [scheme, width] of [
  ['light', 1440],
  ['dark', 1440],
  ['light', 390]
] as const)
  test(`mixed source freshness is reflected in header ${width} ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const stationSignals = STATION_SIGNALS.map((signal) => ({
      ...signal,
      stale: true,
      observedAt: new Date(NOW + 5000).toISOString()
    }));
    await openStation(page, {
      scheme,
      bays,
      stationSignals,
      displayShift: 86400
    });
    const live = page.getByRole('switch', {
      name: /^Live updates: Live · 2 stale/
    });
    await expect(live).toBeChecked();
    await expect(live).toHaveAttribute(
      'aria-label',
      /Recorded source.*shifted/
    );
    await live.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      /Recorded source.*shifted/
    );
    await page.keyboard.press('Escape');
    const tile = page.locator('[data-kpi="pumps-running"]');
    const badge = tile.getByRole('button', { name: 'Stale', exact: true });
    await badge.focus();
    await expect(
      page.getByRole('tooltip', { name: /Source flagged stale/ })
    ).toContainText(/Source flagged stale/);
    await expect(
      page.getByRole('tooltip', { name: /Source flagged stale/ })
    ).toContainText(/Observed.*12:00:05/);
    await expect(
      page.getByRole('tooltip', { name: /Source flagged stale/ })
    ).toContainText(/Read.*12:00:00/);
    await expect(tile.locator('[data-kpi-observed]')).toContainText(
      '5 s ahead of your clock'
    );
    await page.keyboard.press('Escape');
    const name =
      width === 390
        ? '32-station-performance-phone-light'
        : scheme === 'dark'
          ? '34-station-performance-desktop-dark'
          : '35-station-performance-stale-desktop-light';
    await captureFromTop(page);
    await capture(page, name);
    if (width === 390) {
      const ranges = page.getByRole('radiogroup').first();
      const scroller = ranges.locator('..');
      expect(
        await scroller.evaluate((n) => getComputedStyle(n).overflowX)
      ).toBe('auto');
    }
  });
test('Equipment without a schematic falls back to the explicit reference model', async ({
  page
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockMachine(page);
  await mockPositions(page);
  await machineTab(page, 'equipment');
  await expect(page.getByText('3D model', { exact: true })).toBeVisible();
  await expect(
    page.getByText('Reference model', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Process schematic unavailable', { exact: true })
  ).toHaveCount(0);
  await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible({
    timeout: 60000
  });
  await capture(page, 'equipment-fallback-reference-model');
});
test('dense relative Health facts retain row-keyboard and AT explanations at 768', async ({
  page
}) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await openHealth(page, {
    signals: HEALTH_SIGNALS.map((signal) =>
      signal.id === 206
        ? { ...signal, trend: { reason: 'SOURCE_TIMEOUT' } }
        : signal
    ),
    nativeTheme: true,
    compactChrome: true
  });
  const row = page.getByRole('row').filter({
    has: page.getByRole('button', {
      name: 'Inspect signal: Discharge flow',
      exact: true
    })
  });
  const info = row.getByRole('button', {
    name: 'Details for Discharge flow',
    exact: true
  });
  await info.focus();
  await expect(page.getByRole('tooltip')).toContainText(/Observation|2026/);
  const observed = row.locator('td[data-label="Observed"]');
  await expect(observed.locator('time')).toHaveText(/20 s ago/);
  await expect(observed.locator('button')).toHaveCount(0);
  await expect(observed.locator('.mantine-VisuallyHidden-root')).toContainText(
    /2026.*UTC/
  );
  await expect(
    page.getByRole('button', { name: 'Refresh', exact: true })
  ).toBeVisible();
  await page.keyboard.press('Escape');
  for (const [name, status] of [
    ['Seal flush flow', /held no readings/],
    ['Winding temperature', /Trend: Read failed/]
  ] as const) {
    const row = page.getByRole('row').filter({
      has: page.getByRole('button', {
        name: `Inspect signal: ${name}`,
        exact: true
      })
    });
    const trend = row.locator('td[data-label="Trend"]');
    await expect(trend.locator('button')).toHaveCount(0);
    await expect(trend.locator('.mantine-VisuallyHidden-root')).not.toBeEmpty();
    await row
      .getByRole('button', { name: `Details for ${name}`, exact: true })
      .focus();
    await expect(page.getByRole('tooltip', { name: status })).toBeVisible();
    await page.keyboard.press('Escape');
  }
  await captureFromTop(page);
  await capture(page, '22-health-light-tablet-768x1024');
});
test('register imports retain reference scope without claiming field verification', async ({
  page
}) => {
  await mockMachine(page);
  const rows = components(false).map((row, index) => ({
    ...row,
    status: 'draft',
    provenance_kind:
      index === 0 ? 'register' : index === 1 ? 'scada' : 'tag_review',
    review_note:
      'engineering=ENGINEERED_REFERENCE_PENDING_REVIEW compatibility=NOT_APPROVED'
  }));
  await page.route('**/api/assets/registry/18/components/**', (route) =>
    route.fulfill({ json: { count: rows.length, results: rows } })
  );
  await page.route('**/api/assets/parts/**', (route) =>
    route.fulfill({ json: { count: 0, results: [] } })
  );
  await machineTab(page, 'parts');
  const basis = page.getByRole('button', { name: /Register import/ });
  await basis.focus();
  await expect(page.getByRole('tooltip')).toContainText(
    /reference design; not field-verified/
  );
  await expect(page.getByText('Verified', { exact: true })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await capture(page, 'parts-register-import');
});
test('selected location totals appear once and actions share one group', async ({
  page
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const { state } = await mockExplorer(page);
  state.totalLimit = 8;
  await page.goto(
    `/playwright/s8-design.html?${new URLSearchParams({ screen: 'machines', entry: '/machines/index/sites/?location=12', scheme: 'light' })}`
  );
  await expect(
    page.getByRole('region', { name: 'Location totals' })
  ).toBeVisible();
  await expect(page.getByTestId('machine-page-count')).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Choose location', exact: true })
  ).toBeVisible();
  await captureFromTop(page);
  await capture(page, '21-location-selected-light-desktop');
});

for (const width of [1440, 390])
  test(`Details keeps compact identity facts and keyboard reasons ${width}`, async ({
    page
  }) => {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await mockMachine(page);
    await page.route('**/api/assets/machines/18/', (route) =>
      route.fulfill({
        json: {
          pk: 18,
          name: 'Pump 07',
          active: true,
          asset_type: 'pump',
          plant_timezone: 'Asia/Riyadh',
          manufacturer: '',
          model: 'Horizontal split-case pump',
          serial: '',
          description: 'Final effluent duty pump',
          physical_location: null,
          location: ''
        }
      })
    );
    await machineTab(page, '');
    await expect(page.getByText('In service', { exact: true })).toBeVisible();
    const absent = page.getByRole('button', { name: /Manufacturer/ });
    await absent.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      'Manufacturer not recorded'
    );
    const label = page.getByText('Manufacturer', { exact: true });
    expect(
      await label.evaluate((node) => getComputedStyle(node).fontSize)
    ).toBe('14px');
    await captureFromTop(page);
    await capture(
      page,
      width === 390 ? '25-details-phone-light' : '24-details-desktop-light'
    );
  });
