import { expect, test } from '@playwright/test';
import { nativeBaseline } from '../s0/guards';
import { NATIVE_SIGNALS, openPausedPerformance } from './performanceFixtures';
nativeBaseline();
async function choose(page: import('@playwright/test').Page, name: string) {
  await page
    .getByRole('combobox', { name: 'Comparison channels', exact: true })
    .fill(name);
  await page.getByRole('option').filter({ hasText: name }).first().click();
  await page.keyboard.press('Escape');
}
for (const scheme of ['light', 'dark'] as const)
  test(`compatible channels overlay, incompatible units stay separate and personal views restore in ${scheme}`, async ({
    page
  }, info) => {
    const requests = await openPausedPerformance(page, {
      harness: 's3',
      scheme,
      signals: NATIVE_SIGNALS.filter((signal) =>
        /PUMP1_SPEED$|WINDING_TEMPERATURED[12]$/.test(signal.key)
      )
    });
    await page
      .getByRole('button', {
        name: 'Comparisons and saved views',
        exact: true
      })
      .click();
    await choose(page, 'Winding 1');
    await choose(page, 'Winding 2');
    await choose(page, 'Shaft speed');
    await expect(page.locator('[data-comparison-group]')).toHaveCount(2);
    await page.getByLabel('View name', { exact: true }).fill('Investigation');
    await page.getByRole('button', { name: 'Save view', exact: true }).click();
    await expect(
      page.getByText('Display view saved in this browser.', { exact: true })
    ).toBeVisible();
    await page
      .getByRole('button', {
        name: 'Comparisons and saved views',
        exact: true
      })
      .click();
    await page
      .getByRole('button', {
        name: 'Comparisons and saved views',
        exact: true
      })
      .click();
    await expect(page.locator('[data-comparison-group]')).toHaveCount(2);
    expect(requests).toHaveLength(1);
    await page.reload();
    await page
      .getByRole('button', {
        name: 'Comparisons and saved views',
        exact: true
      })
      .click();
    await page
      .getByRole('combobox', { name: 'Saved view', exact: true })
      .click();
    await page
      .getByRole('option', { name: 'Investigation', exact: true })
      .click();
    await page
      .getByRole('button', { name: 'Restore view', exact: true })
      .click();
    await expect(page.locator('[data-comparison-group]')).toHaveCount(2);
    await expect(
      page.getByText('Shared display views', { exact: true })
    ).toBeVisible();
    await page.getByTestId('comparison-workbench').scrollIntoViewIfNeeded();
    await page.getByTestId('comparison-workbench').screenshot({
      path: info.outputPath(`personal-views-${scheme}.png`),
      animations: 'disabled'
    });
  });
test('blocked browser storage never reports a saved view', async ({ page }) => {
  await openPausedPerformance(page, { harness: 's3' });
  await page
    .getByRole('button', { name: 'Comparisons and saved views', exact: true })
    .click();
  await page.evaluate(() => {
    Storage.prototype.setItem = () => {
      throw new Error('Blocked fixture storage');
    };
  });
  await page.getByLabel('View name', { exact: true }).fill('Unsaved');
  await page.getByRole('button', { name: 'Save view', exact: true }).click();
  await expect(page.getByText(/Display view could not be saved/)).toBeVisible();
});
for (const scheme of ['light', 'dark'] as const)
  test(`machine comparisons disclose different plant periods and disable shared cursors in ${scheme}`, async ({
    page
  }, info) => {
    await page.route('**/api/assets/machines/**', (route) =>
      route.fulfill({
        json: {
          count: 2,
          results: [
            { pk: 18, name: 'Current pump', asset_type: 'pump' },
            { pk: 19, name: 'Permitted pump', asset_type: 'pump' }
          ]
        }
      })
    );
    await page.route(
      '**/api/machine-health/machines/19/health/signals/**',
      async (route) => {
        const { signalRows, SIGNALS } = await import('../performance/fixtures');
        return route.fulfill({ json: { results: signalRows(SIGNALS) } });
      }
    );
    const requests: URL[] = [];
    await page.route(
      '**/api/machine-health/machines/19/health/series/**',
      async (route) => {
        const { seriesFor } = await import('../performance/fixtures');
        const url = new URL(route.request().url());
        requests.push(url);
        return route.fulfill({
          json: {
            ...seriesFor(url),
            machine: 19,
            display_shifted: true,
            display_shift_seconds: 86400
          }
        });
      }
    );
    await openPausedPerformance(page, { harness: 's3', scheme });
    await page
      .getByRole('button', {
        name: 'Comparisons and saved views',
        exact: true
      })
      .click();
    await choose(page, 'Shaft speed');
    await page
      .getByRole('combobox', { name: 'Comparison mode', exact: true })
      .click();
    await page
      .getByRole('option', { name: 'Another permitted machine', exact: true })
      .click();
    await page
      .getByRole('combobox', { name: 'Comparison machine', exact: true })
      .click();
    await page
      .getByRole('option', { name: 'Permitted pump', exact: true })
      .click();
    await expect.poll(() => requests.length).toBe(1);
    await expect(
      page.getByText(/Duty, load and configuration remain unverified/)
    ).toBeVisible();
    const alert = page
      .getByRole('alert')
      .filter({ hasText: 'Different plant periods:' });
    await expect(alert).toContainText(
      'Crosshairs are not synchronized between machines.'
    );
    const plots = page
      .getByTestId('comparison-workbench')
      .locator('[data-trend-sync]');
    await expect(plots).toHaveCount(2);
    const ids = await plots.evaluateAll((items) =>
      items.map((item) => item.getAttribute('data-trend-sync'))
    );
    expect(ids[0]).not.toBe(ids[1]);
    await alert.screenshot({
      path: info.outputPath(`clock-alignment-${scheme}.png`),
      animations: 'disabled'
    });
  });
test('saved view capacity and duplicate names preserve every existing view', async ({
  page
}, info) => {
  await openPausedPerformance(page, { harness: 's3' });
  const views = Array.from({ length: 20 }, (_, i) => ({
    name: i === 0 ? 'x'.repeat(80) : `Saved ${i + 1}`,
    search: `original ${i}`,
    section: 'all',
    bindings: [1]
  }));
  const key = 'aimms-performance-views:v1:s3-synthetic:11:18';
  await page.evaluate(
    ({ key, views }) => localStorage.setItem(key, JSON.stringify(views)),
    { key, views }
  );
  await page.reload();
  await page
    .getByRole('button', { name: 'Comparisons and saved views', exact: true })
    .click();
  await page.getByLabel('View name', { exact: true }).fill('Twenty first');
  await page.getByRole('button', { name: 'Save view', exact: true }).click();
  await expect(page.getByText(/All 20 view slots are in use/)).toBeVisible();
  expect(
    await page.evaluate((key) => JSON.parse(localStorage.getItem(key)!), key)
  ).toEqual(views);
  await page.getByTestId('comparison-workbench').screenshot({
    path: info.outputPath('view-capacity-light.png'),
    animations: 'disabled'
  });
  await page.getByLabel('View name', { exact: true }).fill(views[0].name);
  await page.getByRole('button', { name: 'Save view', exact: true }).click();
  await expect(
    page.getByText(
      `A view named ${views[0].name} already exists. Choose another name or explicitly delete that saved view first.`,
      { exact: true }
    )
  ).toBeVisible();
  expect(
    await page.evaluate((key) => JSON.parse(localStorage.getItem(key)!), key)
  ).toEqual(views);
  const panel = page.getByTestId('comparison-workbench');
  expect(
    await panel.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
  ).toBe(true);
  await panel.screenshot({
    path: info.outputPath('view-duplicate-long-light.png'),
    animations: 'disabled'
  });
});
for (const scheme of ['light', 'dark'] as const)
  test(`comparison controls contained in ${scheme}`, async ({ page }, info) => {
    await openPausedPerformance(page, { harness: 's3', scheme });
    await page
      .getByRole('button', {
        name: 'Comparisons and saved views',
        exact: true
      })
      .click();
    await page
      .getByRole('combobox', { name: 'Comparison mode', exact: true })
      .click();
    await page
      .getByRole('option', { name: 'Before / after windows', exact: true })
      .click();
    await choose(page, 'Shaft speed');
    await expect(
      page.getByLabel('Window B from', { exact: true })
    ).toBeVisible();
    const panel = page.getByTestId('comparison-workbench');
    expect(
      await panel.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
    ).toBe(true);
    for (const name of ['Restore view', 'Delete view']) {
      const label = panel
        .getByRole('button', { name, exact: true })
        .locator('.mantine-Button-label');
      expect(
        await label.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
      ).toBe(true);
    }
    await panel.scrollIntoViewIfNeeded();
    await panel.screenshot({
      path: info.outputPath(`comparison-${scheme}.png`),
      animations: 'disabled'
    });
  });

test('before and after use independent bounded windows and query identities', async ({
  page
}, info) => {
  const requests = await openPausedPerformance(page, { harness: 's3' });
  await page
    .getByRole('button', { name: 'Comparisons and saved views', exact: true })
    .click();
  await choose(page, 'Shaft speed');
  await page
    .getByRole('combobox', { name: 'Comparison mode', exact: true })
    .click();
  await page
    .getByRole('option', { name: 'Before / after windows', exact: true })
    .click();
  await page
    .getByRole('button', {
      name: 'Read preceding equal window as B',
      exact: true
    })
    .click();
  await expect.poll(() => requests.length).toBeGreaterThanOrEqual(3);
  const comparisons = requests.filter(
    (url) => url.searchParams.get('bindings')?.split(',').length === 1
  );
  expect(comparisons).toHaveLength(2);
  const windows = comparisons
    .map((url) => ({
      from: Date.parse(url.searchParams.get('from')!),
      to: Date.parse(url.searchParams.get('to')!)
    }))
    .sort((a, b) => a.from - b.from);
  expect(windows[0].to).toBe(windows[1].from);
  expect(windows[0].to - windows[0].from).toBe(3600_000);
  expect(windows[1].to - windows[1].from).toBe(3600_000);
  await expect(
    page.getByText('Window A — selected origin', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Window B — independent origin', { exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await page.getByTestId('comparison-workbench').screenshot({
    path: info.outputPath('aligned-windows-light.png'),
    animations: 'disabled'
  });
});

for (const scheme of ['light', 'dark'] as const) {
  test(`O15 shared views retain creator and engineer default provenance (${scheme})`, async ({
    page
  }, info) => {
    const commands: Record<string, unknown>[] = [];
    let view = {
      id: 9,
      name: 'Engineer operating set',
      config: { search: '', section: 'all', bindings: [100] },
      owner_id: 99,
      owner: 'Synthetic creator',
      is_default: true,
      version: 1,
      recorded_at: '2026-10-18T08:00:00Z',
      updated_by: 'Synthetic engineer',
      updated_at: '2026-10-18T09:00:00Z'
    };
    await page.route(
      '**/api/assets/recorded/18/performance-views/',
      async (route) => {
        if (route.request().method() === 'POST') {
          const body = route.request().postDataJSON();
          commands.push(body);
          view = {
            ...view,
            is_default: body.fields.is_default,
            version: 2,
            updated_by: 'Synthetic engineer',
            updated_at: '2026-10-20T08:00:00Z'
          };
          await route.fulfill({ json: view });
        } else await route.fulfill({ json: { results: [view] } });
      }
    );
    await page.clock.setFixedTime(new Date('2026-10-20T08:00:00Z'));
    await openPausedPerformance(page, {
      harness: 's3',
      scheme,
      harnessParams: { role: 'engineer' },
      now: Date.parse('2026-10-20T08:00:00Z'),
      signals: [NATIVE_SIGNALS[0]]
    });
    await page
      .getByRole('button', {
        name: 'Comparisons and saved views',
        exact: true
      })
      .click();
    const shared = page.getByTestId('shared-display-views');
    await shared.getByRole('button', { name: 'Read shared views' }).click();
    await expect(shared).toContainText('Synthetic creator');
    await expect(shared).toContainText('Synthetic engineer');
    await expect(shared).toContainText('Engineer default');
    await shared.getByRole('button', { name: 'Restore shared view' }).click();
    await expect(
      shared.getByRole('button', { name: 'Delete shared view' })
    ).toHaveCount(0);
    await page.getByTestId('fixture-fetching').waitFor({ state: 'visible' });
    await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
    await shared
      .getByRole('button', { name: 'Remove from defaults' })
      .scrollIntoViewIfNeeded();
    await shared.getByRole('button', { name: 'Remove from defaults' }).click();
    await expect(
      shared.getByRole('button', { name: 'Add to defaults' })
    ).toBeVisible();
    expect(commands).toHaveLength(1);
    expect(commands[0]).toMatchObject({
      operation: 'curate',
      expected_version: 1,
      fields: { id: 9, is_default: false }
    });
    await shared.screenshot({
      path: info.outputPath(`o15-shared-views-${scheme}.png`),
      animations: 'disabled'
    });
  });
}
