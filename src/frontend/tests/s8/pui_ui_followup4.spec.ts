import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { type Page, expect, test } from '@playwright/test';
import { NOW } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import { mockMachine } from '../s0/machineFixtures';
import { components } from '../s2/partsFixtures';
import {
  completedRecords,
  mockFaultHistory,
  mockMachineWork
} from '../s3/machineWorkFixtures';
import { mockPerformance } from '../s3/performanceFixtures';
import { BAYS, openStation } from '../s3/stationFixtures';
import { captureFromTop, mockEquipment } from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(NOW));
});
const directory = resolve(
  '../../data/devscratch/ui-r2',
  process.env.AIMMS_R2_CAPTURE_PHASE ?? 'final4'
);
async function open(page: Page, tab: string) {
  await page.goto(
    `/playwright/s8-design.html?${new URLSearchParams({ screen: 'machine', entry: `/machines/machine/18/${tab}/`, scheme: 'light' })}`
  );
  await expect(
    page
      .getByRole('tablist', { name: 'panel-tabs-asset-machine-' })
      .getByRole('tab', {
        name:
          tab === 'fault-history'
            ? 'Fault History'
            : tab === ''
              ? 'Details'
              : tab === 'equipment'
                ? 'Equipment view'
                : `${tab[0].toUpperCase()}${tab.slice(1)}`,
        exact: true
      })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(
    page.getByText('Loading native panel…', { exact: true })
  ).toHaveCount(0);
}
async function capture(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  await captureFromTop(page);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual(page.viewportSize()!.width);
  await mkdir(directory, { recursive: true });
  await page.screenshot({
    path: resolve(directory, `${name}.png`),
    animations: 'disabled'
  });
}
for (const phone of [false, true])
  test(`production Details missing reasons and identity ${phone ? 'phone' : 'desktop'}`, async ({
    page
  }) => {
    await page.setViewportSize({
      width: phone ? 390 : 1440,
      height: phone ? 844 : 900
    });
    await mockMachine(page);
    await page.route('**/api/assets/machines/18/', async (route) =>
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
    await open(page, '');
    await expect(page.getByText('In service', { exact: true })).toBeVisible();
    await expect(page.getByText('Legacy location label')).toHaveCount(0);
    const missing = page.getByRole('button', { name: /Manufacturer/ });
    await missing.focus();
    await expect(page.getByRole('tooltip')).toContainText(
      'Manufacturer not recorded'
    );
    await page.keyboard.press('Escape');
    await capture(
      page,
      phone ? '25-details-phone-light' : '24-details-desktop-light'
    );
  });
test('production Performance uses labels and compact history while retaining measurements', async ({
  page
}) => {
  await mockMachine(page, { parent: 17, sourceKey: 'P1' });
  await mockPerformance(page);
  await open(page, 'performance');
  await expect(page.getByTestId('pump-performance')).toBeVisible();
  await expect(
    page.getByText('History origin and limits', { exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByRole('button', { name: 'Investigate this period', exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Performance workspace', { exact: true })
  ).toHaveCount(0);
  const speed = page.locator('[data-kpi="speed"]');
  const sparkline = speed.getByRole('img', {
    name: 'Trend over the selected window',
    exact: true
  });
  await expect(speed.getByRole('button')).toHaveCount(0);
  await expect(speed.locator('[data-kpi-value]')).toHaveText('745');
  await expect(speed.getByText('rpm', { exact: true })).toBeVisible();
  await expect(sparkline).toBeVisible();
  await expect(sparkline).not.toHaveAttribute('tabindex');
  await expect(sparkline.locator('path')).toHaveAttribute('d', /M.+L/);
  await expect(speed.locator('[data-kpi-delta]')).toHaveText(
    'Δ +1.2 % / 59.5 min'
  );
  const observed = speed.locator('[data-kpi-observed] time');
  await expect(observed).toHaveText('11:59:40 · 20 s ago');
  await expect(observed).toHaveAttribute(
    'datetime',
    new Date(NOW - 20_000).toISOString()
  );
  await expect(observed.locator('..')).not.toHaveAttribute('tabindex');
  await observed.hover();
  const observationTime = page.getByRole('tooltip', {
    name: /^freshness budget 300 s: 18 Oct 2026 11:59:40 · your clock/
  });
  await expect(observationTime).toBeVisible();
  await expect(observationTime).toContainText('Asia/Riyadh');
  await page.mouse.move(0, 0);
  await page.keyboard.press('Escape');
  await expect(observationTime).toBeHidden();
  await expect(
    page.getByText('Cooling water rise (outlet − inlet)', { exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByText('Air rise (hot − cold)', { exact: true })
  ).toHaveCount(0);
  await capture(page, '26-performance-desktop-light');
  const configured = page.getByTestId('sensor-comparison').filter({
    has: page.getByTestId('sensor-comparison-limits')
  });
  await expect(configured).toHaveCount(1);
  for (const comparison of await configured.all()) {
    const key = comparison.getByTestId('sensor-comparison-limits');
    await expect(key).toContainText('Warning above 125 °C');
    await expect(key).toContainText('Critical above 145 °C');
    const labelTargets = key.locator('p');
    await expect(labelTargets).toHaveCount(2);
    const labels = await labelTargets.all();
    for (let i = 0; i < labels.length; i++)
      for (let j = i + 1; j < labels.length; j++) {
        const a = (await labels[i].boundingBox())!;
        const b = (await labels[j].boundingBox())!;
        expect(
          a.x + a.width <= b.x ||
            b.x + b.width <= a.x ||
            a.y + a.height <= b.y ||
            b.y + b.height <= a.y,
          'Configured limit labels must not overlap'
        ).toBe(true);
      }
    const bounds = (await key.boundingBox())!;
    const plot = (await comparison
      .locator('svg.recharts-surface')
      .boundingBox())!;
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(plot.y);
    await expect(
      comparison.locator('.recharts-reference-line-line')
    ).toHaveCount(2);
  }
  const sensors = page
    .getByText('Current reading by sensor', { exact: true })
    .first();
  await sensors.evaluate((node) => {
    node.style.scrollMarginTop = '84px';
    node.scrollIntoView({ block: 'start' });
  });
  await page.screenshot({
    path: resolve(directory, '31-performance-sensors-desktop-light.png'),
    animations: 'disabled'
  });
});
test('production Maintenance distinguishes absent and unavailable work links', async ({
  page
}) => {
  await mockMachineWork(page, { jobs: 0 });
  await page.route('**/api/assets/maintenance/**', (route) =>
    route.fulfill({
      json:
        route.request().method() === 'OPTIONS'
          ? { actions: {} }
          : {
              count: 2,
              results: [
                { ...completedRecords[1], work_order_link_state: 'none' },
                {
                  ...completedRecords[1],
                  pk: 303,
                  summary: 'Restricted linked visit',
                  work_order_link_state: 'unknown'
                }
              ]
            }
    })
  );
  await open(page, 'maintenance');
  await expect(page.getByText('No open work', { exact: true })).toBeVisible();
  await page.getByText('Completed history', { exact: true }).click();
  const panel = page.getByTestId('machine-maintenance');
  await expect(
    panel.getByRole('columnheader', { name: 'Work Order', exact: true })
  ).toBeVisible();
  for (const [summary, reason, otherReason] of [
    [
      'Legacy service visit',
      'No linked work order',
      'Work order link not available'
    ],
    [
      'Restricted linked visit',
      'Work order link not available',
      'No linked work order'
    ]
  ]) {
    const row = panel
      .getByRole('row')
      .filter({ has: page.getByText(summary, { exact: true }) });
    const work = row.getByRole('cell').filter({ hasText: reason });
    await expect(work).toHaveCount(1);
    const dash = work.locator('span').filter({ hasText: reason }).first();
    await expect(dash).toBeVisible();
    expect(
      await dash.evaluate((node) =>
        [...node.childNodes]
          .filter((child) => child.nodeType === Node.TEXT_NODE)
          .map((child) => child.textContent)
          .join('')
          .trim()
      )
    ).toBe('—');
    await expect(dash).not.toHaveAttribute('tabindex');
    await expect(work.getByRole('button')).toHaveCount(0);
    const information = row.getByRole('button', {
      name: 'Record information',
      exact: true
    });
    await page.mouse.move(0, 0);
    await page.keyboard.press('Tab');
    await information.focus();
    await expect(information).toBeFocused();
    await expect(information).toHaveAttribute('aria-describedby', /\S+/);
    const tooltip = page.locator(
      `[role="tooltip"][id="${await information.getAttribute('aria-describedby')}"]`
    );
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText(reason);
    await expect(tooltip).not.toContainText(otherReason);
    await expect(tooltip).toContainText('Downtime not recorded');
    await expect(information).toHaveAccessibleDescription(
      await tooltip.innerText()
    );
    await page.keyboard.press('Escape');
    await expect(tooltip).toBeHidden();
    await information.blur();
    await expect(row.getByRole('link')).toHaveCount(0);
    await expect(row.getByText('0 min', { exact: true })).toHaveCount(0);
  }
  await expect(
    page.getByText('No work order you can open', { exact: true })
  ).toHaveCount(0);
  await capture(page, '27-maintenance-desktop-light');
});
test('production Fault history retains occurrences without developer prose', async ({
  page
}) => {
  await mockFaultHistory(page);
  await open(page, 'fault-history');
  await expect(page.getByTestId('fault-occurrences')).toBeVisible();
  await expect(
    page.getByText('Declared fault codes', { exact: false })
  ).toHaveCount(0);
  await expect(page.getByText('gap days', { exact: false })).toHaveCount(0);
  await capture(page, '28-fault-history-desktop-light');
});
test('production Attachments expose readable headers and wrap comments', async ({
  page
}) => {
  await mockMachine(page);
  await page.route('**/api/attachment/**', (route) =>
    route.fulfill({
      json:
        route.request().method() === 'OPTIONS'
          ? { actions: {} }
          : {
              count: 1,
              results: [
                {
                  pk: 1,
                  attachment:
                    '/media/Horizontal-synchronous-motor-operating-and-maintenance-manual-reference-2026.pdf',
                  filename: 'Pump manual.pdf',
                  comment: `Pump operating and maintenance manual. ${'Approved operating instructions. '.repeat(12)}`,
                  upload_date: '2026-10-20T08:00:00Z',
                  file_size: 20480,
                  user_detail: null
                }
              ]
            }
    })
  );
  await open(page, 'attachments');
  for (const name of ['Attachment', 'Comment', 'Uploaded', 'Size'])
    await expect(
      page.locator('thead').getByText(name, { exact: true })
    ).toBeVisible();
  await capture(page, '29-attachments-desktop-light');
});
test('production Equipment defaults to schematic and offers an explicit reference model', async ({
  page
}) => {
  await mockEquipment(page);
  await open(page, 'equipment');
  await expect(page.locator('[data-equipment-view="schematic"]')).toBeVisible();
  await expect(
    page.getByText('Ask about this position', { exact: true })
  ).toHaveCount(0);
  await page.getByText('3D model', { exact: true }).click();
  await expect(page.getByText('Reference model', { exact: true })).toHaveCount(
    1
  );
  await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible({
    timeout: 60000
  });
  await capture(page, '30-equipment-view-desktop-light');
});
test('production Parts basis merges source and never elevates reference imports to verified', async ({
  page
}) => {
  await mockMachine(page);
  const rows = components(false).map((row, index) => ({
    ...row,
    status: 'draft',
    provenance_kind:
      index === 0 ? 'register' : index === 1 ? 'scada' : 'tag_review',
    provenance_display:
      index === 0 ? 'Equipment register import' : row.provenance_display,
    review_note:
      'engineering=ENGINEERED_REFERENCE_PENDING_REVIEW compatibility=NOT_APPROVED'
  }));
  await page.route('**/api/assets/registry/18/components/**', (route) =>
    route.fulfill({ json: { count: rows.length, results: rows } })
  );
  await page.route('**/api/assets/parts/**', (route) =>
    route.fulfill({ json: { count: 0, results: [] } })
  );
  await open(page, 'parts');
  for (const label of ['Register import', 'Inferred from tags', 'Tag review'])
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  await expect(
    page.getByRole('columnheader', { name: 'Source', exact: true })
  ).toHaveCount(0);
  await expect(page.getByText('Verified', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /Register import/ }).focus();
  await expect(page.getByRole('tooltip')).toContainText(
    'Engineering review pending · Compatibility not approved'
  );
  await expect(page.getByRole('tooltip')).toContainText('not field-verified');
  await expect(page.getByRole('tooltip')).not.toContainText('f9006');
});

test('production station totals require all reporting bays', async ({
  page
}) => {
  await openStation(page, {
    bays: BAYS.map((bay, index) => ({ ...bay, noHistory: index === 1 }))
  });
  const power = page.locator('[data-kpi="plant-power"]');
  await expect(power).toBeVisible();
  await expect(power.locator('[data-kpi-value]')).toHaveText('—');
  await page.keyboard.press('Tab');
  await power
    .getByRole('button', { name: /1 of 4 bays not reporting/ })
    .focus();
  await expect(
    page.getByRole('tooltip', {
      name: /1 of 4 bays not reporting/
    })
  ).toBeVisible();
  await expect(
    page.locator('[data-kpi="pumps-running"] [data-kpi-value]')
  ).toHaveText(/of 4/);
});
