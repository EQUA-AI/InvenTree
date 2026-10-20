import type { Page } from '@playwright/test';
import type { MimicData } from '../../src/pages/assets/health/mimic/types';
import { mockMachine } from '../s0/machineFixtures';
import { mockPositions } from '../s2/equipmentFixtures';

export function schematicFixture(state = 'running'): MimicData {
  const point = {
    pointer: '/dex/PUMP1_POWER',
    label: 'Motor power',
    group: 'Motor',
    value: state === 'running' ? 7.5 : null,
    unit: 'MW',
    quality: 'good',
    observed_at: '2026-10-20T08:00:00Z',
    age_seconds: state === 'stale' ? 500 : 2,
    unchanged_for_seconds: null,
    reason:
      state === 'stale' ? 'stale' : state === 'unknown' ? 'no_data' : null,
    condition: 'unknown',
    thresholds_configured: false
  };
  return {
    station: 17,
    name: 'Synthetic station',
    generated_at: '2026-10-20T08:00:02Z',
    enabled: true,
    source: { pk: 1, name: 'Synthetic SCADA' },
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
    bays: [
      {
        key: 'P1',
        machine: 18,
        name: 'Synthetic pump',
        active: true,
        state,
        points: {}
      }
    ],
    selected_unit: 'P1',
    points: { [point.pointer]: point },
    totals: {},
    alarms: (['open', 'acknowledged', 'suppressed', null] as const).map(
      (status, index) => ({
        ...point,
        pointer: `/dex/ALARM${index}`,
        label: `Synthetic alarm ${index + 1}`,
        value: 95,
        unit: 'degC',
        condition: 'warning',
        thresholds_configured: true,
        machine: 18,
        anomaly: 901 + index,
        anomaly_status: status,
        severity: 'warning'
      })
    ),
    unconfigured_thresholds: 1
  };
}

export async function mockEquipment(page: Page, state = 'running') {
  await mockMachine(page, {
    parent: 17,
    parentName: 'Synthetic station',
    sourceKey: 'P1'
  });
  await mockPositions(page);
  const data = schematicFixture(state);
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({ json: data })
  );
  return data;
}

export async function openEquipment(
  page: Page,
  schematic = false,
  scheme = 'light',
  query: Record<string, string> = {}
) {
  const entry = `/machines/machine/18/equipment/${schematic ? '?view=schematic' : ''}`;
  await page.goto(
    `/playwright/s8-design.html?${new URLSearchParams({ screen: 'equipment-model', entry, scheme, ...query })}`,
    { waitUntil: 'domcontentloaded' }
  );
  await page
    .getByText(
      'S8 synthetic fixture · native panels · no live plant connection',
      { exact: true }
    )
    .waitFor();
  await page
    .getByText('Loading native panel…', { exact: true })
    .waitFor({ state: 'hidden', timeout: 30_000 });
  if (!schematic)
    await page
      .locator('[data-scene-phase="ready"]')
      .waitFor({ timeout: 60_000 });
  else
    await page
      .getByText('Pump unit: P1', { exact: true })
      .waitFor({ timeout: 30_000 });
}

export async function openExplorer(
  page: Page,
  entry: string,
  scheme = 'light'
) {
  await page.goto(
    `/playwright/s8-design.html?${new URLSearchParams({ screen: 'machines', entry, scheme })}`,
    { waitUntil: 'domcontentloaded' }
  );
  await page
    .getByText(
      'S8 synthetic fixture · native panels · no live plant connection',
      { exact: true }
    )
    .waitFor();
  await page
    .getByText('Loading native panel…', { exact: true })
    .waitFor({ state: 'hidden', timeout: 30_000 });
  await page
    .locator('table, [role="alert"]')
    .first()
    .waitFor({ timeout: 30_000 });
}
export async function openFaultHistory(
  page: Page,
  options: { scheme?: 'light' | 'dark'; query?: string } = {}
) {
  await page.goto(
    `/playwright/s8-design.html?${new URLSearchParams({ screen: 'fault-history', scheme: options.scheme ?? 'light', entry: `/machines/machine/18/fault-history/${options.query ?? ''}` })}`,
    { waitUntil: 'domcontentloaded' }
  );
  await page
    .getByText(
      'S8 synthetic fixture · native panels · no live plant connection',
      { exact: true }
    )
    .waitFor();
  await page
    .getByText('Loading native panel…', { exact: true })
    .waitFor({ state: 'hidden', timeout: 30_000 });
  await page
    .locator('[data-occurrence], [role="alert"]')
    .first()
    .waitFor({ timeout: 30_000 });
  if (options.query?.includes('occurrence='))
    await page.getByTestId('occurrence-evidence').waitFor({ timeout: 30_000 });
}

/** Capture the top viewport after native selected-tab/evidence scrolling settles. */
export async function captureFromTop(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
            resolve();
          })
        )
      )
  );
}
