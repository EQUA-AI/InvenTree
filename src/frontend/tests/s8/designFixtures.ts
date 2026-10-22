import { readFileSync } from 'node:fs';
import type { Page } from '@playwright/test';
import type { MimicData } from '../../src/pages/assets/health/mimic/types';
import { mockMachine } from '../s0/machineFixtures';
import { mockPositions } from '../s2/equipmentFixtures';

export function schematicFixture(state = 'running'): MimicData {
  const layout = JSON.parse(
    readFileSync(
      new URL(
        '../../../backend/InvenTree/machine_health/layouts/pumphouse.layout.json',
        import.meta.url
      ),
      'utf8'
    )
  );
  const point = {
    pointer: '/dex/PUMP1_ACTIVE_POWER',
    label: 'Motor power',
    group: 'Motor',
    value: state === 'running' ? 7.5 : null,
    unit: 'kW',
    quality: state === 'unknown' ? 'unknown' : 'good',
    observed_at:
      state === 'unknown'
        ? null
        : state === 'stale'
          ? '2026-10-20T07:51:42Z'
          : '2026-10-20T08:00:00Z',
    age_seconds: state === 'unknown' ? null : state === 'stale' ? 500 : 2,
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
    last_poll_at: '2026-10-20T08:00:00Z',
    last_error_at: null,
    last_success_at: '2026-10-20T08:00:00Z',
    plant_timezone: null,
    display_shifted: false,
    display_shift_seconds: 0,
    last_error_code: '',
    layout,
    station_points: {},
    bays: [
      {
        key: 'P1',
        machine: 18,
        name: 'Synthetic pump',
        active: true,
        state,
        alarm: 'warning',
        points: {}
      }
    ],
    selected_unit: 'P1',
    points: Object.fromEntries(
      layout.elements
        .filter((e: { view: string }) => e.view === 'unit')
        .map(
          (e: { pointer: string; role: string; id: string; label: string }) => {
            const pointer = e.pointer
              .replaceAll('{pump_number}', '1')
              .replaceAll('{pump}', 'P1');
            const [value, unit] =
              e.role === 'status'
                ? ['R', '']
                : e.id === 'pump-flow'
                  ? [0.4, 'm³/s']
                  : e.id === 'pump-pressure'
                    ? [3.2, 'bar']
                    : e.id === 'pump-speed'
                      ? [980, 'rpm']
                      : [7.5, 'kW'];
            return [
              pointer,
              {
                ...point,
                pointer,
                label: e.label,
                value: state === 'running' ? value : null,
                unit
              }
            ];
          }
        )
    ),
    totals: {},
    alarms: (['open', 'acknowledged', 'suppressed', null] as const).map(
      (status, index) => ({
        ...point,
        pointer: `/dex/ALARM${index}`,
        label: `Synthetic alarm ${index + 1}`,
        value: state === 'running' ? 95 : null,
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
  const entry = `/machines/machine/18/equipment/${schematic ? '?view=schematic' : '?view=model'}`;
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
  else if (query.state === 'query-error')
    await page.getByRole('alert').first().waitFor();
  else
    await page
      .getByRole('heading', { name: 'Synthetic pump', exact: true })
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
    .locator(
      'table:visible, [data-machine-card]:visible, [role="alert"]:visible, h5:visible'
    )
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
