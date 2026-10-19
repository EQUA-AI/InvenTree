import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { mockPositions } from './equipmentFixtures';

nativeBaseline();

// Equipment view reads the machine's position records once it mounts.
test.beforeEach(async ({ page }) => {
  await mockPositions(page);
});

const STATION = 17;

/** The station's mimic for one bay, as `/station/17/mimic/?unit=` serves it. */
function stationMimic(unit: string | null) {
  const pointer = unit ? `/dex/PUMP${unit.slice(1)}_POWER` : null;
  return {
    station: STATION,
    name: 'Fixture station',
    generated_at: '2026-09-13T12:00:02Z',
    enabled: true,
    source: { pk: 1, name: 'Fixture source' },
    last_poll_at: null,
    last_error_code: '',
    layout: {
      version: 1,
      review_status: 'provisional',
      elements: [
        {
          id: 'pump-power',
          pointer: '/dex/PUMP{pump_number}_POWER',
          view: 'unit',
          role: 'value',
          label: 'Power',
          x: 370,
          y: 120
        }
      ]
    },
    station_points: {},
    bays: ['P1', 'P2'].map((key) => ({
      key,
      name: key,
      active: true,
      state: 'running',
      points: {}
    })),
    selected_unit: unit,
    points: pointer
      ? {
          [pointer]: {
            pointer,
            label: pointer,
            group: 'Motor',
            value: 7.5,
            unit: 'MW',
            quality: 'good',
            observed_at: '2026-09-13T12:00:00Z',
            age_seconds: 2,
            reason: null,
            condition: 'unknown',
            thresholds_configured: false
          }
        }
      : {},
    totals: {
      power: {
        value: null,
        unit: 'MW',
        derived: true,
        reason: 'incomplete',
        contributors: []
      }
    },
    alarms: [],
    unconfigured_thresholds: 1
  };
}

async function mockStation(page: Page) {
  const reads: (string | null)[] = [];
  await page.route(
    `**/api/machine-health/station/${STATION}/mimic/**`,
    (route) => {
      const unit = new URL(route.request().url()).searchParams.get('unit');
      reads.push(unit);
      return route.fulfill({ json: stationMimic(unit) });
    }
  );
  return reads;
}

const machinePage = (page: Page, entry: string) =>
  page.goto(
    `/playwright/s0-native.html?surface=machine&entry=${encodeURIComponent(entry)}`
  );
const destination = (page: Page) => page.getByText(/^\/machines\/machine\//);
const tab = (page: Page, name: string) =>
  page.getByRole('tab', { name, exact: true });
const chooseView = (page: Page, name: string) =>
  page
    .getByRole('radiogroup', { name: 'Equipment view' })
    .getByText(name, { exact: true })
    .click();

test('the schematic is the secondary view, and switching back keeps the model as it was', async ({
  page
}) => {
  await page.goto('/playwright/s2-equipment.html?kind=panel&schematic=yes');
  await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible();
  await expect(
    page
      .getByRole('radiogroup', { name: 'Equipment view' })
      .getByRole('radio', {
        name: '3D model'
      })
  ).toBeChecked();
  await page.locator('[data-position="bearing"]').click();
  await page
    .getByRole('radiogroup', { name: 'Model view mode' })
    .getByText('Exploded', { exact: true })
    .click();
  await chooseView(page, 'Process schematic');
  await expect(page.getByTestId('fixture-schematic')).toBeVisible();
  await expect(page.locator('canvas.aimms-twin-canvas')).toHaveCount(0);
  await expect(page.getByTestId('fixture-location')).toHaveText(
    '/fixture?view=schematic'
  );
  await chooseView(page, '3D model');
  await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible();
  await expect(page.locator('[data-selected-position]')).toHaveText(
    'Selected: Motor drive-end bearing'
  );
  await expect(
    page
      .getByRole('radiogroup', { name: 'Model view mode' })
      .getByRole('radio', { name: 'Exploded' })
  ).toBeChecked();
  await expect(page.getByTestId('fixture-location')).toHaveText('/fixture');
});

test('a link can open the schematic first, and a pump without one has no switch', async ({
  page
}) => {
  await page.goto(
    `/playwright/s2-equipment.html?kind=panel&schematic=yes&entry=${encodeURIComponent('/fixture?view=schematic')}`
  );
  await expect(page.getByTestId('fixture-schematic')).toBeVisible();
  await expect(page.locator('canvas.aimms-twin-canvas')).toHaveCount(0);
  // No schematic: no switch, and the link's view is ignored.
  await page.goto(
    `/playwright/s2-equipment.html?kind=panel&entry=${encodeURIComponent('/fixture?view=schematic')}`
  );
  await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible();
  await expect(
    page.getByRole('radiogroup', { name: 'Equipment view' })
  ).toHaveCount(0);
});

test('a pump in a station has one Equipment tab whose schematic is its native mimic', async ({
  page
}, testInfo) => {
  await mockMachine(page, {
    parent: STATION,
    parentName: 'Fixture station',
    sourceKey: 'P1'
  });
  const reads = await mockStation(page);
  await machinePage(page, '/machines/machine/18/equipment/');
  await expect(tab(page, 'Equipment view')).toHaveAttribute(
    'aria-selected',
    'true'
  );
  // No competing tab beside it.
  await expect(tab(page, 'Pump mimic')).toHaveCount(0);
  await chooseView(page, 'Process schematic');
  await expect(page.getByText('Pump unit: P1')).toBeVisible();
  expect(reads).toContain('P1');
  await expect(destination(page)).toHaveText(
    '/machines/machine/18/equipment/?view=schematic'
  );
  // The station crumb opens the station's own mimic, not Details.
  await expect(
    page.getByRole('link', {
      name: 'breadcrumb-1-fixture-station',
      exact: true
    })
  ).toHaveAttribute('href', /\/machines\/machine\/17\/mimic$/);
  await page.screenshot({
    path: testInfo.outputPath('equipment-schematic.png'),
    animations: 'disabled'
  });
});

test('the old pump mimic link opens Equipment view on the schematic, keeping what it carried', async ({
  page
}) => {
  await mockMachine(page, {
    parent: STATION,
    parentName: 'Fixture station',
    sourceKey: 'P1'
  });
  await mockStation(page);
  await machinePage(page, '/machines/machine/18/mimic?from=sites&location=12');
  await expect(destination(page)).toHaveText(
    '/machines/machine/18/equipment/?from=sites&location=12&view=schematic'
  );
  await expect(tab(page, 'Equipment view')).toHaveAttribute(
    'aria-selected',
    'true'
  );
  await expect(page.getByText('Pump unit: P1')).toBeVisible();
});

test("Health's mimic link leads to the pump's schematic", async ({ page }) => {
  await mockMachine(page, {
    parent: STATION,
    parentName: 'Fixture station',
    sourceKey: 'P1'
  });
  await mockStation(page);
  await page.route('**/api/machine-health/machines/18/health/', (route) =>
    route.fulfill({
      json: {
        state: 'normal',
        configured: true,
        signal_count: 0,
        stale_signal_count: 0,
        degraded_data: false,
        last_observed_at: null,
        anomaly_counts: { critical: 0, warning: 0, info: 0 },
        active_anomaly_count: 0,
        sources: []
      }
    })
  );
  await page.route(
    '**/api/machine-health/machines/18/health/data-range/**',
    (route) =>
      route.fulfill({ json: { available: false, from: null, to: null } })
  );
  for (const read of ['signals', 'anomalies'])
    await page.route(
      `**/api/machine-health/machines/18/health/${read}/**`,
      (route) => route.fulfill({ json: { count: 0, results: [] } })
    );
  await machinePage(page, '/machines/machine/18/health/');
  const link = page.getByRole('link', {
    name: 'See the readings on the mimic'
  });
  await expect(link).toHaveAttribute(
    'href',
    /\/machines\/machine\/18\/equipment\/\?view=schematic$/
  );
  await link.click();
  await expect(tab(page, 'Equipment view')).toHaveAttribute(
    'aria-selected',
    'true'
  );
  await expect(page.getByText('Pump unit: P1')).toBeVisible();
});

test('a station keeps its own mimic tab and gets no 3D view', async ({
  page
}) => {
  await mockMachine(page, { assetType: 'pumphouse' });
  await page.route('**/api/machine-health/station/18/mimic/**', (route) =>
    route.fulfill({ json: { ...stationMimic(null), station: 18 } })
  );
  await machinePage(page, '/machines/machine/18/mimic/');
  await expect(tab(page, 'Pumphouse mimic')).toHaveAttribute(
    'aria-selected',
    'true'
  );
  await expect(tab(page, 'Equipment view')).toHaveCount(0);
  await expect(destination(page)).toHaveText('/machines/machine/18/mimic/');
});

test("following the station crumb or the station mimic link from a pump's schematic reaches the station", async ({
  page
}) => {
  await mockMachine(page, {
    parent: STATION,
    parentName: 'Fixture station',
    sourceKey: 'P1'
  });
  await mockStation(page);
  await page.route(`**/api/assets/machines/${STATION}/`, (route) =>
    route.fulfill({
      json: {
        pk: STATION,
        name: 'Fixture station',
        active: true,
        client: 1,
        asset_type: 'pumphouse',
        parent: null,
        parent_name: null,
        source_key: 'PH_1',
        location: '',
        manufacturer: '',
        model: '',
        serial: '',
        description: '',
        barcode_hash: ''
      }
    })
  );
  for (const follow of [
    (page: Page) =>
      page
        .getByRole('link', {
          name: 'breadcrumb-1-fixture-station',
          exact: true
        })
        .click(),
    (page: Page) =>
      page
        .getByRole('link', { name: 'Open station mimic', exact: true })
        .click()
  ]) {
    await machinePage(page, '/machines/machine/18/equipment/?view=schematic');
    await expect(page.getByText('Pump unit: P1')).toBeVisible();
    await follow(page);
    // The station's own mimic - not a bounce back to the pump it came from.
    await expect(tab(page, 'Pumphouse mimic')).toHaveAttribute(
      'aria-selected',
      'true'
    );
    await expect(destination(page)).toHaveText(
      /^\/machines\/machine\/17\/mimic/
    );
  }
});
