import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { type Locator, type Page, expect, test } from '@playwright/test';
import { NOW } from '../performance/fixtures';
import { nativeBaseline } from '../s0/guards';
import {
  completedRecords,
  mockFaultHistory,
  mockMachineWork,
  rollup
} from '../s3/machineWorkFixtures';
import { nonText, readable } from './contrast';
import { captureFromTop } from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();
test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date(NOW));
});

const directory = resolve('../../data/devscratch/ui-r2/final7');
const codes = [
  { code: 'NORTH-P3-BRG', count: 2 },
  { code: 'WEST-MOTOR-OT', description: 'Motor temperature high', count: 1 },
  { code: 'EAST-SEAL-ALM', count: 1 }
];

async function open(
  page: Page,
  tab: 'maintenance' | 'fault-history',
  scheme: 'light' | 'dark',
  query = ''
) {
  const search = new URLSearchParams({
    screen: 'machine',
    entry: `/machines/machine/18/${tab}/${query}`,
    scheme
  });
  await page.goto(`/playwright/s8-design.html?${search}`);
  await expect(
    page
      .getByRole('tablist', { name: 'panel-tabs-asset-machine-' })
      .getByRole('tab', {
        name: tab === 'fault-history' ? 'Fault History' : 'Maintenance',
        exact: true
      })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(
    page.getByText('Loading native panel…', { exact: true })
  ).toHaveCount(0);
}

async function capture(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  await page.keyboard.press('Escape');
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement)
      document.activeElement.blur();
  });
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

async function singleLine(locator: Locator) {
  expect(
    await locator.evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      return new Set(
        Array.from(range.getClientRects())
          .filter((rect) => rect.width > 0 && rect.height > 0)
          .map((rect) => Math.round(rect.top))
      ).size;
    })
  ).toBe(1);
}

async function faultFixture(page: Page) {
  await mockFaultHistory(page);
  await page.route('**/api/assets/machines/18/fault-history/', (route) =>
    route.fulfill({
      json: {
        ...rollup,
        fault_dictionary: {
          'EAST-SEAL-ALM': { description: 'Seal pressure low' }
        },
        observed: {
          ...rollup.observed,
          failure_codes: { ...rollup.observed.failure_codes, top: codes }
        }
      }
    })
  );
}

for (const scheme of ['light', 'dark'] as const) {
  test(`CMMS Fault history keeps dictionary descriptions and unknown codes ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await faultFixture(page);
    await open(page, 'fault-history', scheme);
    const panel = page.getByTestId('fault-occurrences');
    await expect(panel).toBeVisible();
    for (const name of ['Occurrences', 'Maintenance history'])
      await expect(
        page.getByRole('heading', { name, exact: true, level: 4 })
      ).toBeVisible();
    await expect(
      page.getByRole('heading', {
        name: 'Alarm and fault investigation',
        exact: true
      })
    ).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: 'Refresh', exact: true })
    ).toHaveCount(1);
    await expect(page.getByText('NORTH-P3-BRG', { exact: true })).toBeVisible();
    await expect(
      page.getByText('Motor temperature high', { exact: true })
    ).toBeVisible();
    await expect(
      page.getByText('Seal pressure low', { exact: true })
    ).toBeVisible();
    await expect(
      page.getByText('Unclassified fault', { exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByText('Reading back inside its limits.', { exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByText('Observed span, not event duration', { exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByText('No owning work you can open', { exact: true })
    ).toHaveCount(0);

    const observed = page
      .getByRole('columnheader', { name: 'Observed', exact: true })
      .getByRole('button');
    await observed.focus();
    await expect(
      page.getByRole('tooltip', {
        name: 'Observed span, not event duration',
        exact: true
      })
    ).toBeVisible();
    await page.keyboard.press('Escape');
    const resolved = panel.getByRole('button').filter({
      has: page.locator('[data-alarm-status="resolved"]')
    });
    await resolved.focus();
    const resolvedTip = page.getByRole('tooltip', {
      name: /Resolved does not always mean cleared/
    });
    await expect(resolvedTip).toBeVisible();
    await expect(resolvedTip).toContainText('Reading back inside its limits.');
    await page.keyboard.press('Escape');
    for (const status of ['open', 'acknowledged', 'resolved']) {
      const badge = panel.locator(`[data-alarm-status="${status}"]`);
      await singleLine(badge.locator('.mantine-Badge-label'));
      expect((await badge.boundingBox())!.height).toBe(24);
    }
    const owning = panel.getByRole('row').filter({
      has: page.getByText('Discharge pressure below its warning limit', {
        exact: true
      })
    });
    await expect(
      owning.getByRole('link', { name: 'WO-73', exact: true })
    ).toBeVisible();
    await expect(
      owning.getByText('In progress', { exact: true })
    ).toBeVisible();
    await capture(
      page,
      scheme === 'light'
        ? '28-fault-history-desktop-light'
        : '37-fault-history-desktop-dark'
    );
  });

  test(`CMMS Maintenance collapses uniform absence while retaining false and zero ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const requests = await mockMachineWork(page, { jobs: 0 });
    const rows = [
      {
        ...completedRecords[1],
        pk: 401,
        summary: 'Bearing service recorded',
        work_order_link_state: 'none',
        downtime_minutes: 0
      },
      {
        ...completedRecords[1],
        pk: 402,
        summary: 'Pump service recorded',
        work_order_link_state: 'none',
        downtime_minutes: null
      }
    ];
    let reads = 0;
    await page.route('**/api/assets/maintenance/**', (route) => {
      if (route.request().method() === 'OPTIONS')
        return route.fulfill({ json: { actions: {} } });
      reads += 1;
      return route.fulfill({ json: { count: rows.length, results: rows } });
    });
    await open(page, 'maintenance', scheme, '?work_view=completed');
    const panel = page.getByTestId('machine-maintenance');
    await expect(
      panel.getByText('Bearing service recorded', { exact: true })
    ).toBeVisible();
    for (const name of [
      'Type',
      'Component',
      'Work Order',
      'Performed By',
      'Outcome'
    ])
      await expect(
        panel.getByRole('columnheader', { name: new RegExp(`^${name}`) })
      ).toHaveCount(0);
    for (const name of ['Date', 'Summary'])
      await expect(
        panel
          .locator('thead')
          .getByRole('button', { name: new RegExp(`^${name}\\b`) })
      ).toBeVisible();
    for (const name of ['Downtime', 'Verified', 'Follow-up'])
      await expect(
        panel.getByRole('columnheader', { name, exact: true })
      ).toBeVisible();
    const first = panel.getByRole('row').filter({
      has: page.getByText('Bearing service recorded', { exact: true })
    });
    await expect(first.getByText('0 min', { exact: true })).toBeVisible();
    await expect(first.getByText('No', { exact: true })).toBeVisible();
    await expect(
      first.getByText('Not verified', { exact: true })
    ).toBeVisible();
    const verification = first.getByText('Not verified', { exact: true });
    await readable(verification);
    await nonText(verification.locator('..'), first);
    await expect(first.getByRole('button')).toHaveCount(1);
    const info = first.getByRole('button', {
      name: 'Record information',
      exact: true
    });
    await info.focus();
    await expect(
      page.getByRole('tooltip', { name: /^No linked work order/ })
    ).toBeVisible();
    const rowTip = page.getByRole('tooltip', { name: /^No linked work order/ });
    await expect(rowTip).toContainText('Component not recorded');
    await expect(rowTip).not.toContainText('Downtime not recorded');
    await expect(rowTip).not.toContainText('Follow-up status not recorded');
    await page.keyboard.press('Escape');
    await expect(
      panel.getByRole('button', { name: 'Refresh', exact: true })
    ).toHaveCount(1);
    await expect(
      panel.getByRole('button', { name: 'Refresh counts', exact: true })
    ).toHaveCount(0);
    const previousReads = reads;
    const previousCounts = requests.summary.length;
    await panel.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect.poll(() => reads).toBeGreaterThan(previousReads);
    await expect
      .poll(() => requests.summary.length)
      .toBeGreaterThan(previousCounts);
    await capture(
      page,
      scheme === 'light'
        ? '27-maintenance-desktop-light'
        : '36-maintenance-desktop-dark'
    );

    await panel
      .getByRole('button', { name: 'Select columns', exact: true })
      .click();
    const type = page.getByRole('checkbox', { name: /^Type/ });
    await expect(type).not.toBeChecked();
    await expect(type).toHaveAccessibleName(/Work type not recorded/);
    await page
      .getByRole('menuitem')
      .filter({ has: type })
      .locator('label')
      .hover();
    await expect(
      page.getByRole('tooltip', { name: 'Work type not recorded', exact: true })
    ).toBeVisible();
    await type.check();
    await page.keyboard.press('Escape');
    await expect(
      panel.getByRole('columnheader', { name: /^Type/ })
    ).toBeVisible();
    await expect(first.getByRole('button')).toHaveCount(1);
    await expect(
      first.getByRole('cell').filter({ hasText: 'Work type not recorded' })
    ).toContainText('—');
  });

  test(`CMMS phone Fault states remain single line ${scheme}`, async ({
    page
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await faultFixture(page);
    await open(page, 'fault-history', scheme);
    const panel = page.getByTestId('fault-occurrences');
    await expect(panel).toBeVisible();
    for (const status of ['open', 'acknowledged', 'resolved']) {
      const badge = panel.locator(`[data-alarm-status="${status}"]`);
      await singleLine(badge.locator('.mantine-Badge-label'));
      const box = (await badge.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(390);
    }
    await capture(
      page,
      scheme === 'light'
        ? '38-fault-history-phone-light'
        : '39-fault-history-phone-dark'
    );
  });
}

test('CMMS Maintenance keeps absent and unavailable work links distinguishable', async ({
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
                {
                  ...completedRecords[1],
                  pk: 501,
                  summary: 'Unlinked service',
                  work_order_link_state: 'none'
                },
                {
                  ...completedRecords[1],
                  pk: 502,
                  summary: 'Restricted service',
                  work_order_link_state: 'unknown'
                }
              ]
            }
    })
  );
  await open(page, 'maintenance', 'light', '?work_view=completed');
  const panel = page.getByTestId('machine-maintenance');
  await expect(
    panel.getByRole('columnheader', { name: 'Work Order', exact: true })
  ).toBeVisible();
  for (const [summary, reason] of [
    ['Unlinked service', 'No linked work order'],
    ['Restricted service', 'Work order link not available']
  ]) {
    const row = panel
      .getByRole('row')
      .filter({ has: page.getByText(summary, { exact: true }) });
    await row
      .getByRole('button', { name: 'Record information', exact: true })
      .focus();
    await expect(
      page.getByRole('tooltip', { name: new RegExp(`^${reason}`) })
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(row.getByRole('link')).toHaveCount(0);
  }
});
