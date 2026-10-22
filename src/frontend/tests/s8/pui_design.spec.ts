import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { mockExplorer } from '../s1/machineExplorerFixtures';
import {
  mockFaultHistory,
  occurrenceDetail,
  occurrences
} from '../s3/machineWorkFixtures';
import { SIGNALS, mockPerformance } from '../s3/performanceFixtures';
import { readable } from './contrast';
import {
  mockEquipment,
  openEquipment,
  openExplorer,
  openFaultHistory
} from './designFixtures';
import { nativeThemeFixtures } from './themeFixtures';

nativeBaseline();
nativeThemeFixtures();

test('Machines separates loaded-page counts, stale signals and unavailable operating facts', async ({
  page
}) => {
  await mockExplorer(page);
  await openExplorer(page, '/machines/index/sites/?location=12', 'dark');
  const summary = page.getByRole('region', { name: 'Loaded page summary' });
  await expect(summary).toContainText('25 of 80');
  await expect(summary.getByTestId('machine-page-count')).toHaveAttribute(
    'aria-label',
    /Stale signals on 10 machines/
  );
  await expect(summary.getByTestId('machine-page-count')).toHaveAttribute(
    'aria-label',
    /Stale-signal coverage unavailable for 1 machine/
  );
  await expect(summary.getByTestId('machine-page-count')).toHaveAttribute(
    'aria-label',
    /Telemetry, run\/stop and communication status unavailable here\./
  );
  await page
    .getByRole('button', { name: 'Location details', exact: true })
    .click();
  await expect(page.getByText(/Times: your clock/)).toBeVisible();
  await expect(page.getByTestId('machine-count-view')).toHaveText('80');
  await expect(
    page.locator('td[data-condition="offline"]').first()
  ).toContainText('No current data');
  await expect(page.locator('td[data-condition="unavailable"]')).toHaveText(
    'Condition unavailable · could not be checked'
  );
  await expect(summary).not.toContainText('Running');
});

test('missing condition leaves stale-signal coverage unavailable instead of zero', async ({
  page
}) => {
  const fixture = await mockExplorer(page);
  for (const machine of fixture.machines) machine.condition = null;
  await openExplorer(page, '/machines/index/sites/?location=12');
  const summary = page.getByRole('region', { name: 'Loaded page summary' });
  await expect(summary.getByTestId('machine-page-count')).toHaveAttribute(
    'aria-label',
    /Stale-signal coverage unavailable for 25 machines/
  );
  await expect(summary).not.toContainText('Stale signals on 0 machines');
  for (const name of ['Critical · 0', 'Warning · 0']) {
    const count = summary.getByRole('button', { name, exact: true });
    await expect(count).toBeDisabled();
    await readable(count);
  }
});

test('an empty station has zero recorded machines without a healthy or connected verdict', async ({
  page
}) => {
  const fixture = await mockExplorer(page);
  fixture.state.totalLimit = 0;
  fixture.location.counts.direct_machines = 0;
  fixture.location.counts.total_machines = 0;
  fixture.location.counts.direct_open_work_orders = 0;
  fixture.location.counts.total_open_work_orders = 0;
  await openExplorer(page, '/machines/index/sites/?location=12');
  await expect(
    page.getByRole('heading', {
      name: 'No machines at this location',
      exact: true
    })
  ).toBeVisible();
  const summary = page.getByRole('region', { name: 'Loaded page summary' });
  await expect(summary).toHaveCount(0);
  await expect(page.getByRole('columnheader')).toHaveCount(0);
  await expect(page.getByText('0 / 0', { exact: true })).toHaveCount(0);
});

test('failed machine refresh hides previous summary and retains a retry', async ({
  page
}) => {
  const fixture = await mockExplorer(page);
  await openExplorer(page, '/machines/index/machines/');
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toBeVisible();
  fixture.state.status = 503;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Machines unavailable' })
  ).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toHaveCount(0);
  fixture.state.status = 200;
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toBeVisible();
});

for (const state of ['running', 'stale', 'unknown']) {
  test(`schematic ${state} state never implies measured flow or communication`, async ({
    page
  }) => {
    await mockEquipment(page, state);
    await openEquipment(page, true, 'dark');
    await expect(
      page.getByRole('img', { name: 'Pump unit schematic' })
    ).toBeVisible();
    await expect(
      page
        .getByLabel('Process data')
        .getByText('Flow direction', { exact: true })
    ).toBeVisible();
    await expect(
      page
        .getByLabel('Process data')
        .getByText('Communication status', { exact: true })
    ).toBeVisible();
    await expect(
      page.getByText('Reference schematic · physical arrangement unverified')
    ).toBeVisible();
    if (state === 'stale')
      await expect(page.getByText('7.5 MW', { exact: true })).toHaveCount(0);
    await expect(
      page.getByText('Active · not acknowledged', { exact: true })
    ).toBeVisible();
    await expect(
      page.getByText('Active · acknowledged', { exact: true })
    ).toBeVisible();
    await expect(page.getByText('Dismissed', { exact: true })).toBeVisible();
    const unknown = page.locator('tr[data-anomaly="904"]');
    await expect(unknown).toContainText('Alarm state unavailable');
    await expect(
      unknown.getByRole('button', { name: 'Acknowledge' })
    ).toHaveCount(0);
    await expect(
      unknown.getByRole('button', { name: 'Dismiss', exact: true })
    ).toHaveCount(0);
    await expect(
      page.getByText(
        'Acknowledgement records that someone saw the application alarm; it does not clear the condition.'
      )
    ).toBeVisible();
  });
}

test('polling failure is labelled as a polling error and hides the old readings', async ({
  page
}) => {
  const data = await mockEquipment(page);
  data.last_error_code = 'NETWORK';
  await openEquipment(page, true);
  await expect(
    page.getByRole('alert').filter({ hasText: 'Polling error' })
  ).toBeVisible();
  await page.route('**/api/machine-health/station/17/mimic/**', (route) =>
    route.fulfill({ status: 503, json: {} })
  );
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(
    page.getByText('Refresh to retry; cached values are hidden.')
  ).toBeVisible();
  await expect(
    page.getByRole('img', { name: 'Pump unit schematic' })
  ).toHaveCount(0);
});

test('model reference label leads and keyboard selection keeps exact component records', async ({
  page
}) => {
  await mockEquipment(page);
  await openEquipment(page, false, 'dark');
  await expect(page.locator('[data-scene-phase="ready"]')).toBeVisible();
  await expect(
    page.getByText('Reference model · geometry unverified').first()
  ).toBeVisible();
  const bearing = page.locator('[data-position="bearing"]');
  await bearing.focus();
  await bearing.press('Enter');
  await expect(page.locator('[data-selected-position]')).toContainText(
    'Motor drive-end bearing'
  );
  await expect(
    page.getByText('DE bearing temperature', { exact: true })
  ).toBeVisible();
  await page.locator('[data-position="impeller"]').click();
  await expect(
    page.getByText('DE bearing temperature', { exact: true })
  ).toHaveCount(0);
});

test('Fault History distinguishes active, acknowledged and resolved without asserting cleared', async ({
  page
}) => {
  await mockFaultHistory(page);
  await openFaultHistory(page, { scheme: 'dark' });
  await expect(page.locator('[data-occurrence="902"]').first()).toContainText(
    'Active'
  );
  await expect(page.locator('[data-occurrence="900"]').first()).toContainText(
    'Active · acknowledged'
  );
  await expect(page.locator('[data-occurrence="901"]').first()).toContainText(
    'Reading back inside its limits.'
  );
  await expect(
    page.getByText(
      'Resolved does not always mean cleared. Acknowledged conditions remain active.'
    )
  ).toBeVisible();
});

test('event trend adds a labelled bounded context and preserves the canonical occurrence', async ({
  page
}, info) => {
  const requests = await mockPerformance(page, {
    signals: SIGNALS.slice(0, 3)
  });
  await mockFaultHistory(page);
  await openFaultHistory(page, { query: '?occurrence=901' });
  const link = page.getByRole('link', {
    name: 'Trend around first observation · 30 min before and after'
  });
  await expect(link).toBeVisible();
  const url = new URL(
    (await link.getAttribute('href'))!,
    'https://example.invalid'
  );
  expect(url.pathname).toBe('/machines/machine/18/performance/');
  expect(url.searchParams.get('occurrence')).toBe('901');
  expect(url.searchParams.get('history_clock')).toBe('recorded');
  expect(url.searchParams.get('history_from')).toBe('2026-10-12T01:44:05.000Z');
  expect(url.searchParams.get('history_to')).toBe('2026-10-12T02:44:05.000Z');
  const before = requests.length;
  await link.click();
  await expect(page.getByText('Exceptions', { exact: true })).toBeVisible({
    timeout: 30_000
  });
  await expect.poll(() => requests.length).toBe(before + 1);
  expect(requests.at(-1)!.searchParams.get('from')).toBe(
    '2026-10-12T01:44:05.000Z'
  );
  expect(requests.at(-1)!.searchParams.get('to')).toBe(
    '2026-10-12T02:44:05.000Z'
  );
  await expect(page.getByTestId('linked-evidence')).toContainText(
    'Retained occurrence 901'
  );
  await page.screenshot({
    path: info.outputPath('event-context-trend.png'),
    fullPage: false
  });
});

test('resolved without a clearing reading retains its full reason and does not assert cleared', async ({
  page
}, info) => {
  await mockFaultHistory(page);
  const reason =
    'Closed without a clearing reading: this binding no longer carries a threshold, or it stopped reporting. The condition was not observed to end.';
  await page.route('**/fault-history/occurrences/?*', (route) =>
    route.fulfill({
      json: {
        rule: 'fault_occurrences.v1',
        machine: 18,
        count: 1,
        limit: 25,
        offset: 0,
        results: [{ ...occurrences[1], resolution_note: reason }]
      }
    })
  );
  await page.route('**/fault-history/occurrences/901/', (route) =>
    route.fulfill({
      json: { ...occurrenceDetail(901), resolution_note: reason }
    })
  );
  await openFaultHistory(page, { scheme: 'dark', query: '?occurrence=901' });
  const row = page.getByTestId('occurrence-evidence');
  await expect(row).toContainText('Resolved');
  await expect(row).toContainText(reason);
  await expect(row.getByText('Cleared', { exact: true })).toHaveCount(0);
  const reasonText = row.getByText(reason, { exact: true }).first();
  const showFull = row.getByRole('button', {
    name: 'Show full note',
    exact: true
  });
  if (await showFull.count()) await showFull.first().click();
  await expect
    .poll(() =>
      reasonText.evaluate((el) => el.scrollHeight <= el.clientHeight + 1)
    )
    .toBe(true);
  for (const badge of await row.locator('.mantine-Badge-label').all()) {
    expect(
      await badge.evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
      'Severity and outcome labels must stay whole beside a long reason'
    ).toBe(true);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1
    )
  ).toBe(true);
  await page.screenshot({
    path: info.outputPath('resolved-unassessable-dark.png'),
    fullPage: true
  });
});

test('a mapped numeric zero discharge flow is shown with its source unit without implying direction', async ({
  page
}) => {
  const data = await mockEquipment(page, 'unknown');
  data.points['/pd/P1/dv'] = {
    ...data.points['/pd/P1/dv'],
    pointer: '/pd/P1/dv',
    label: 'Discharge flow',
    value: 0,
    unit: 'm^3/s',
    quality: 'good',
    reason: null
  };
  await openEquipment(page, true);
  await expect(
    page.getByLabel('Process data').getByText('0 m^3/s', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByLabel('Process data').getByText('Flow direction', { exact: true })
  ).toBeVisible();
});

// Exercise wider glyphs and taller default text lines without reducing any
// production text size. This is a layout stress check, not a macOS renderer.
for (const scheme of ['light', 'dark'] as const) {
  for (const screen of ['machines', 'cutaway', 'resolved']) {
    test(`${screen} first item survives wider font metrics in ${scheme}`, async ({
      page
    }, info) => {
      const phone = info.project.name.endsWith('phone');
      test.skip(
        !phone && screen === 'machines',
        'Machine card density applies on phone'
      );
      if (!phone) await page.setViewportSize({ width: 1440, height: 900 });
      if (screen === 'machines') {
        await mockExplorer(page);
        await openExplorer(page, '/machines/index/sites/?location=12', scheme);
      } else if (screen === 'cutaway') {
        await mockEquipment(page);
        await openEquipment(page, false, scheme);
        const modes = page.locator('[aria-label="Model view mode"]');
        // Check every selected/unselected label immediately after each change,
        // including the formerly low-contrast intermediate selection colors.
        for (const mode of ['Exploded', 'Assembled', 'Cutaway']) {
          await modes.getByText(mode, { exact: true }).click();
          for (const label of await modes.locator('label').all()) {
            await readable(label, 14);
          }
        }
        const views = page.locator('[aria-label="Equipment view"]');
        for (const label of await views.locator('label').all()) {
          await readable(label);
        }
      } else {
        await mockFaultHistory(page);
        await openFaultHistory(page, { scheme, query: '?occurrence=901' });
      }
      await page.addStyleTag({
        content: `
        :root {
          --mantine-font-family: Arial, sans-serif !important;
          --mantine-font-family-headings: Arial, sans-serif !important;
          --mantine-line-height: 1.65 !important;
        }
        body { letter-spacing: 0.2px; }
      `
      });
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => window.scrollTo(0, 0));
      const first = !phone
        ? screen === 'cutaway'
          ? page.getByRole('list', { name: 'Model components' })
          : page
              .locator(
                '[data-testid="occurrence-evidence"] [data-alarm-status]'
              )
              .first()
        : screen === 'machines'
          ? page.locator('[data-machine]').first()
          : screen === 'cutaway'
            ? page.locator('.aimms-twin-canvas')
            : page.locator('[data-occurrence] [data-alarm-status]').first();
      await expect(first).toBeVisible();
      await expect
        .poll(async () => {
          const box = (await first.boundingBox())!;
          // The first card is complete; the model and occurrence state begin
          // in the first viewport, leaving the full model available to scroll.
          return !phone
            ? screen === 'cutaway'
              ? box.y
              : box.y + box.height
            : screen === 'machines'
              ? box.y + box.height
              : screen === 'cutaway'
                ? box.y + 80
                : box.y + box.height;
        })
        .toBeLessThanOrEqual(phone ? 844 : 900);
      if (phone && screen === 'resolved') {
        const box = (await first.boundingBox())!;
        expect(
          box.y + box.height,
          'At least 4px of phone fold headroom'
        ).toBeLessThanOrEqual(840);
      }
      const caveats = page.locator('[class*="_caveat_"]');
      for (const caveat of await caveats.all()) {
        if (!(await caveat.isVisible())) continue;
        expect(
          await caveat.evaluate((el) =>
            Number.parseFloat(getComputedStyle(el).fontSize)
          )
        ).toBeGreaterThanOrEqual(14);
      }
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth)
      ).toBeLessThanOrEqual(phone ? 390 : 1440);
      await writeFile(
        info.outputPath('font-density-metrics.json'),
        JSON.stringify(
          {
            phone,
            screen,
            scheme,
            first: await first.boundingBox(),
            font: await first.evaluate((el) => ({
              family: getComputedStyle(el).fontFamily,
              size: getComputedStyle(el).fontSize,
              lineHeight: getComputedStyle(el).lineHeight
            }))
          },
          null,
          2
        )
      );
      await page.screenshot({
        path: info.outputPath(`${screen}-${scheme}-wide-font.png`),
        animations: 'disabled'
      });
      await page.screenshot({
        path: info.outputPath(`${screen}-${scheme}-wide-font-full.png`),
        fullPage: true,
        animations: 'disabled'
      });
    });
  }
}
