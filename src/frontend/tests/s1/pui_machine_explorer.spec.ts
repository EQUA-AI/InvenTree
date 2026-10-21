import type { Locator } from '@playwright/test';
import { expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import {
  explorerParams,
  machineReturnLink,
  mockExplorer,
  openExplorer,
  showExplorerControls
} from './machineExplorerFixtures';

nativeBaseline();
const column = (scope: Locator, name: string) =>
  scope
    .getByRole('columnheader', { name, exact: true })
    .or(scope.getByText(name, { exact: true }))
    .first();

test('machine return restores direct location search alarms page and selected path', async ({
  page
}, testInfo) => {
  const fixture = await mockExplorer(page);
  const origin =
    '/machines/index/sites/?location=12&scope=direct&search=pump&has_alarms=true&page=2';
  await openExplorer(page, origin);
  await expect(
    page.getByRole('textbox', { name: 'Search machines', exact: true })
  ).toHaveValue('pump');
  await expect(
    page.getByRole('switch', { name: 'In alarm only', exact: true })
  ).toBeChecked();
  await expect(
    page.getByRole('switch', { name: 'Include sublocations', exact: true })
  ).not.toBeChecked();
  const firstMachine = page
    .getByTestId('machine-results')
    .getByRole('link', { name: /^Synthetic pump/ })
    .first();
  await expect(firstMachine).toBeVisible();
  const machineName = await firstMachine.innerText();
  await expect(firstMachine).toHaveAttribute(
    'href',
    /from=sites&location=12&scope=direct&search=pump&has_alarms=true&page=2$/
  );
  await firstMachine.focus();
  await firstMachine.press('Enter');
  await expect(page.getByText('Serial Number', { exact: true })).toBeVisible();
  const breadcrumb = machineReturnLink(page);
  expect(await breadcrumb.getAttribute('href')).toContain(origin);
  await breadcrumb.focus();
  await breadcrumb.press('Enter');
  await expect(
    page
      .getByTestId('machine-results')
      .getByRole('link', { name: machineName, exact: true })
  ).toBeVisible();
  await showExplorerControls(page);
  await expect(
    page.getByRole('textbox', { name: 'Search machines', exact: true })
  ).toHaveValue('pump');
  await expect(
    page
      .getByTestId('machine-results')
      .getByRole('link', { name: machineName, exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('machine-count-view')).toHaveText('60');
  await expect(page.getByTestId('location-totals-note')).toBeVisible();
  if (testInfo.project.name === 's1-phone') {
    await page
      .getByRole('button', { name: 'Choose location', exact: true })
      .click();
  }
  await expect(
    page
      .getByRole('treeitem')
      .filter({
        has: page.getByRole('button', {
          name: 'Synthetic maintenance site',
          exact: true
        })
      })
      .first()
  ).toHaveAttribute('aria-selected', 'true');
  expect(fixture.requests.every((request) => request.method === 'GET')).toBe(
    true
  );
  expect(
    fixture.requests
      .filter((request) => !request.params.has('machine'))
      .every(
        (request) =>
          request.params.get('search') === 'pump' &&
          request.params.get('has_alarms') === 'true' &&
          request.params.get('include_descendants') === 'false' &&
          request.params.get('offset') === '25'
      )
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath('machines-return.png'),
    fullPage: true,
    animations: 'disabled'
  });
});

for (const source of ['machines', 'unassigned']) {
  test(`${source} return restores its own list without applying location-only filters`, async ({
    page
  }) => {
    const fixture = await mockExplorer(page);
    const origin = `/machines/index/${source}/?search=pump&has_alarms=true&page=2`;
    await openExplorer(page, origin);
    const first = page
      .getByTestId('machine-results')
      .getByRole('link', { name: /^Synthetic pump/ })
      .first();
    await expect(first).toBeVisible();
    const machineName = await first.innerText();
    await first.click();
    await expect(
      page.getByText('Serial Number', { exact: true })
    ).toBeVisible();
    const back = machineReturnLink(page);
    expect(await back.getAttribute('href')).toContain(origin);
    await back.click();
    await expect(
      page
        .getByTestId('machine-results')
        .getByRole('link', { name: machineName, exact: true })
    ).toBeVisible();
    await showExplorerControls(page);
    await expect(
      page
        .getByTestId('machine-results')
        .getByRole('link', { name: machineName, exact: true })
    ).toBeVisible();
    await expect(
      page.getByRole('textbox', { name: 'Search machines', exact: true })
    ).toHaveValue('pump');
    await expect(
      page.getByRole('button', { name: 'Move selected', exact: true })
    ).toHaveCount(0);
    await expect(page.getByRole('checkbox')).toHaveCount(0);
    const requests = fixture.requests.filter(
      (request) => !request.params.has('machine')
    );
    expect(requests.every((request) => !request.params.has('location'))).toBe(
      true
    );
    expect(
      requests.every(
        (request) =>
          request.params.get('unassigned') === String(source === 'unassigned')
      )
    ).toBe(true);
    expect(
      requests.every((request) => request.params.get('offset') === '25')
    ).toBe(true);
  });
}

test('filter and physical scope changes reset page without conflating alarm counts with location totals', async ({
  page
}, testInfo) => {
  const fixture = await mockExplorer(page);
  await openExplorer(
    page,
    '/machines/index/sites/?location=12&scope=direct&page=2',
    'dark'
  );
  const alarms = page.getByRole('switch', {
    name: 'In alarm only',
    exact: true
  });
  const search = page.getByRole('textbox', {
    name: 'Search machines',
    exact: true
  });
  const pagination = page.getByRole('navigation', {
    name: 'Machine results pagination',
    exact: true
  });
  await expect(
    pagination.getByRole('button', { name: '2', exact: true })
  ).toHaveAttribute('aria-current', 'page');
  await alarms.check();
  await expect(page.getByText('50 machines', { exact: true })).toBeVisible();
  expect((await explorerParams(page)).has('page')).toBe(false);
  await expect(page.getByTestId('machine-count-view')).toHaveText('60');
  await expect(page.getByTestId('location-totals-note')).toBeVisible();
  await pagination.getByRole('button', { name: '2', exact: true }).click();
  await search.fill('S1-26');
  await expect(page.getByText('1 machines', { exact: true })).toBeVisible();
  expect((await explorerParams(page)).has('page')).toBe(false);
  const latest = fixture.requests.at(-1)!.params;
  expect(latest.get('search')).toBe('S1-26');
  expect(latest.get('offset')).toBe('0');
  expect(latest.get('has_alarms')).toBe('true');
  await search.fill('');
  await expect(page.getByText('50 machines', { exact: true })).toBeVisible();
  await pagination.getByRole('button', { name: '2', exact: true }).click();
  await page
    .getByRole('switch', { name: 'Include sublocations', exact: true })
    .check();
  await expect(page.getByTestId('machine-count-view')).toHaveText('80');
  expect((await explorerParams(page)).has('page')).toBe(false);
  expect((await explorerParams(page)).has('scope')).toBe(false);
  await expect(page.getByText('Name, serial or manufacturer')).toBeVisible();
  await expect(
    page.getByRole('columnheader', { name: 'Active record', exact: true })
  ).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath('machines-dark-filtered.png'),
    fullPage: true,
    animations: 'disabled'
  });
  await search.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath('machines-filtered-viewport.png'),
    animations: 'disabled'
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
});

test('location selection starts a fresh population while explicit history restores prior filters and page', async ({
  page
}, testInfo) => {
  await mockExplorer(page);
  await openExplorer(
    page,
    '/machines/index/sites/?location=12&search=pump&has_alarms=true&page=2'
  );
  if (testInfo.project.name === 's1-phone') {
    await page
      .getByRole('button', { name: 'Choose location', exact: true })
      .click();
  }
  await page
    .getByRole('button', {
      name: 'Expand Synthetic maintenance site',
      exact: true
    })
    .click();
  await page
    .getByRole('button', { name: 'Synthetic pump hall', exact: true })
    .click();
  await expect(
    page.getByRole('heading', { name: 'Synthetic pump hall', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toContainText('20 of 20 machines');
  await showExplorerControls(page);
  await expect(
    page.getByRole('textbox', { name: 'Search machines', exact: true })
  ).toHaveValue('');
  await expect(
    page.getByRole('switch', { name: 'In alarm only', exact: true })
  ).not.toBeChecked();
  await expect(page.getByText('20 machines', { exact: true })).toBeVisible();
  expect((await explorerParams(page)).get('location')).toBe('21');
  expect((await explorerParams(page)).has('page')).toBe(false);
  await page.getByRole('button', { name: 'Fixture back', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toContainText('of 50 machines');
  await showExplorerControls(page);
  await expect(
    page.getByRole('textbox', { name: 'Search machines', exact: true })
  ).toHaveValue('pump');
  await expect(
    page.getByRole('switch', { name: 'In alarm only', exact: true })
  ).toBeChecked();
  await expect(
    page
      .getByRole('navigation', {
        name: 'Machine results pagination',
        exact: true
      })
      .getByRole('button', { name: '2', exact: true })
  ).toHaveAttribute('aria-current', 'page');
  await page
    .getByRole('button', { name: 'Fixture forward', exact: true })
    .click();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toContainText('20 of 20 machines');
  await showExplorerControls(page);
  await expect(page.getByText('20 machines', { exact: true })).toBeVisible();
});

for (const status of [403, 500]) {
  test(`a ${status} refresh hides cached rows and counts and retries the exact URL scope`, async ({
    page
  }) => {
    const fixture = await mockExplorer(page);
    await openExplorer(
      page,
      '/machines/index/sites/?location=12&scope=direct&search=pump&has_alarms=true&page=2'
    );
    await expect(page.getByText('50 machines', { exact: true })).toBeVisible();
    fixture.state.status = status;
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Machines unavailable' })
    ).toBeVisible();
    await expect(page.getByTestId('machine-results')).toHaveCount(0);
    await expect(page.getByText('50 machines', { exact: true })).toHaveCount(0);
    await expect(page.getByText('0 machines', { exact: true })).toHaveCount(0);
    await expect(page.getByText('No machines match your search.')).toHaveCount(
      0
    );
    fixture.state.status = 200;
    await page.getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(page.getByText('50 machines', { exact: true })).toBeVisible();
    expect(fixture.requests.at(-1)!.params.get('offset')).toBe('25');
    expect(fixture.requests.at(-1)!.params.get('search')).toBe('pump');
    expect(fixture.requests.at(-1)!.params.get('has_alarms')).toBe('true');
  });
}

test('an out-of-range returned page reconciles to the last real page without changing filters', async ({
  page
}) => {
  const fixture = await mockExplorer(page);
  await openExplorer(
    page,
    '/machines/index/sites/?location=12&scope=direct&search=pump&has_alarms=true&page=4'
  );
  await expect(
    page
      .getByTestId('machine-results')
      .getByRole('link', { name: 'Synthetic pump 026', exact: true })
  ).toBeVisible();
  expect((await explorerParams(page)).get('page')).toBe('2');
  expect(
    fixture.requests
      .filter((request) => !request.params.has('machine'))
      .map((request) => request.params.get('offset'))
  ).toEqual(['75', '25']);
  await expect(
    page.getByRole('switch', { name: 'In alarm only', exact: true })
  ).toBeChecked();
  await expect(page.getByText('No machines match your search.')).toHaveCount(0);
});

test('successful search misses remain distinct from source failure and clear within the same location', async ({
  page
}) => {
  const fixture = await mockExplorer(page);
  await openExplorer(
    page,
    '/machines/index/sites/?location=12&scope=direct&search=missing&has_alarms=true&page=2'
  );
  await expect(page.getByText('No machines match your search.')).toBeVisible();
  await expect(page.getByText('0 machines', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Clear search', exact: true }).click();
  await expect(page.getByText('50 machines', { exact: true })).toBeVisible();
  const params = await explorerParams(page);
  expect(params.get('location')).toBe('12');
  expect(params.get('scope')).toBe('direct');
  expect(params.get('has_alarms')).toBe('true');
  expect(params.has('search')).toBe(false);
  expect(params.has('page')).toBe(false);
  expect(fixture.requests.every((request) => request.method === 'GET')).toBe(
    true
  );
});

test('each machine shows its open Work apart from alarms, and an unknown count is not a zero', async ({
  page
}, testInfo) => {
  await mockExplorer(page);
  for (const scheme of ['light', 'dark']) {
    await openExplorer(page, '/machines/index/machines/', scheme);
    const table = page.getByTestId('machine-results');
    await expect(column(table, 'Work')).toBeVisible();
    const row = (name: string) =>
      table.locator('[data-machine]').filter({ hasText: name });
    // Busy: count and the highest native priority.
    const busy = row('Synthetic pump 001');
    await expect(busy.locator('td[data-open-work="2"]')).toContainText(
      '2 open'
    );
    await expect(busy.locator('td[data-open-work="2"]')).toContainText('High');
    await expect(
      row('Synthetic pump 002').locator('td[data-open-work="1"]')
    ).toContainText('Low');
    // Unresolved scope reads as not available, never as a quiet dash.
    await expect(
      row('Synthetic pump 003').locator('td[data-open-work="unavailable"]')
    ).toHaveText('Unavailable · could not be checked');
    // Explicitly none.
    await expect(
      row('Synthetic pump 006').locator('td[data-open-work="0"]')
    ).toHaveText('0');
    // Alarms and Work stay different facts on the same row.
    await expect(busy.locator('td[data-alarms]')).toBeVisible();
    // Status labels read whole at every width: no truncated badge text.
    for (const badge of await table
      .locator(
        'td[data-open-work] .mantine-Badge-label, td[data-alarms] .mantine-Badge-label'
      )
      .all()) {
      expect(
        await badge.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
      ).toBe(true);
    }
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
    // Phones keep the native table's own horizontal scroll; bring Work in.
    await busy.locator('td[data-open-work="2"]').scrollIntoViewIfNeeded();
    await table.screenshot({
      path: testInfo.outputPath(`machine-work-${scheme}.png`),
      animations: 'disabled'
    });
  }
});

test('criticality is the declared classification, apart from alarms and work priority', async ({
  page
}, testInfo) => {
  await mockExplorer(page);
  for (const scheme of ['light', 'dark']) {
    await openExplorer(page, '/machines/index/machines/', scheme);
    const table = page.getByTestId('machine-results');
    await expect(column(table, 'Criticality')).toBeVisible();
    const cell = (name: string) =>
      table
        .locator('[data-machine]')
        .filter({ hasText: name })
        .locator('td[data-criticality]');
    await expect(cell('Synthetic pump 001')).toHaveText('Critical');
    await expect(cell('Synthetic pump 002')).toHaveText('High');
    await expect(cell('Synthetic pump 004')).toHaveText('Medium');
    await expect(cell('Synthetic pump 005')).toHaveText('Low');
    // Nothing declared is said, not shown as a level.
    await expect(cell('Synthetic pump 003')).toHaveText('Not declared');
    // Plain text: never a coloured badge like an alarm or a priority.
    await expect(
      table.locator('td[data-criticality] .mantine-Badge-root')
    ).toHaveCount(0);
    for (const value of await table.locator('td[data-criticality] p').all())
      expect(
        await value.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
      ).toBe(true);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
    await cell('Synthetic pump 001').scrollIntoViewIfNeeded();
    await table.screenshot({
      path: testInfo.outputPath(`machine-criticality-${scheme}.png`),
      animations: 'disabled'
    });
  }
});

test('the criticality filter narrows on the server, resets the page and survives the machine round trip', async ({
  page
}, testInfo) => {
  const fixture = await mockExplorer(page);
  await openExplorer(page, '/machines/index/machines/?page=2');
  const filter = page.getByRole('combobox', {
    name: 'Criticality',
    exact: true
  });
  await filter.click();
  await page.getByRole('option', { name: 'Critical', exact: true }).click();
  await expect(filter).toHaveValue('Criticality: Critical');
  await expect(page.getByText('22 machines', { exact: true })).toBeVisible();
  const request = fixture.requests.at(-1)!.params;
  expect(request.get('criticality')).toBe('critical');
  expect(request.get('offset')).toBe('0');
  const params = await explorerParams(page);
  expect(params.get('criticality')).toBe('critical');
  expect(params.has('page')).toBe(false);
  await page.screenshot({
    path: testInfo.outputPath('machine-criticality-filter.png'),
    animations: 'disabled'
  });
  const machine = page.getByRole('link', {
    name: 'Synthetic pump 006',
    exact: true
  });
  expect(
    new URL(
      (await machine.getAttribute('href'))!,
      'https://example.invalid'
    ).searchParams.get('criticality')
  ).toBe('critical');

  fixture.state.noCriticalityMatch = true;
  await openExplorer(page, '/machines/index/machines/?criticality=low');
  await expect(
    page.getByText('No machines match these filters.', { exact: true })
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Clear filters', exact: true })
    .click();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toContainText('of 110 machines');
  await showExplorerControls(page);
  await expect(filter).toHaveValue('Any criticality');
  expect((await explorerParams(page)).has('criticality')).toBe(false);
  expect(fixture.requests.at(-1)!.params.has('criticality')).toBe(false);
});

test('each machine shows its Health condition with coverage on its own line, and an unknown condition is not normal', async ({
  page
}, testInfo) => {
  await mockExplorer(page);
  for (const scheme of ['light', 'dark']) {
    await openExplorer(page, '/machines/index/machines/', scheme);
    const table = page.getByTestId('machine-results');
    await expect(column(table, 'Condition')).toBeVisible();
    const cell = (name: string) =>
      table
        .locator('[data-machine]')
        .filter({ hasText: name })
        .locator('td[data-condition]');
    // Full coverage: the state alone.
    await expect(cell('Synthetic pump 001')).toHaveText('Normal');
    // Limited coverage says how, apart from the state.
    await expect(cell('Synthetic pump 002')).toHaveText(
      'Warning2 of 6 signals stale'
    );
    await expect(cell('Synthetic pump 004')).toHaveText(
      'UnknownNo signals mapped'
    );
    await expect(cell('Synthetic pump 005')).toHaveText(
      'OfflineNo current data'
    );
    await expect(cell('Synthetic pump 008')).toHaveText(
      'CriticalPoor-quality readings'
    );
    // Not judged reads as not available, never as a quiet normal.
    await expect(cell('Synthetic pump 003')).toHaveAttribute(
      'data-condition',
      'unavailable'
    );
    await expect(cell('Synthetic pump 003')).toHaveText(
      'Condition unavailable · could not be checked'
    );
    // Condition, Alarms, Work and Criticality stay separate cells.
    const busy = table
      .locator('[data-machine]')
      .filter({ hasText: 'Synthetic pump 001' });
    for (const column of [
      'td[data-condition]',
      'td[data-alarms]',
      'td[data-open-work]',
      'td[data-criticality]'
    ])
      await expect(busy.locator(column)).toHaveCount(1);
    for (const label of await table
      .locator('td[data-condition] .mantine-Badge-label, td[data-condition] p')
      .all())
      expect(
        await label.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)
      ).toBe(true);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth)
    ).toBeLessThanOrEqual(page.viewportSize()!.width);
    // Desktop density: every column is visible without scrolling sideways.
    if (testInfo.project.name === 's1-desktop')
      expect(
        await table.evaluate((el) => {
          let node = el.parentElement;
          while (
            node &&
            !['auto', 'scroll'].includes(getComputedStyle(node).overflowX)
          )
            node = node.parentElement;
          return !!node && node.scrollWidth <= node.clientWidth + 1;
        })
      ).toBe(true);
    await cell('Synthetic pump 001').scrollIntoViewIfNeeded();
    await table.screenshot({
      path: testInfo.outputPath(`machine-condition-${scheme}.png`),
      animations: 'disabled'
    });
  }
});
