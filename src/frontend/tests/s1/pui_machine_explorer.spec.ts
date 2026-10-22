import type { Locator, Page } from '@playwright/test';
import { expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import { nonText, readable, textSweep } from '../s8/contrast';
import {
  explorerParams,
  machineReturnLink,
  mockExplorer,
  openExplorer,
  showExplorerControls
} from './machineExplorerFixtures';

nativeBaseline();
const unavailableMachineFacts =
  'Condition could not be checked · Open work could not be checked · Criticality not declared';

async function keyboardTooltip(page: Page, target: Locator) {
  await page.mouse.move(0, 0);
  await page.keyboard.press('Tab');
  await target.focus();
  await expect(target).toBeFocused();
  await expect(target).toHaveAttribute('aria-describedby', /\S+/);
  await expect(page.getByRole('tooltip')).toHaveCount(1);
}

async function expectLocationTotals(
  page: Page,
  count: number,
  descendants = false
) {
  const totals = page.getByRole('region', {
    name: 'Location totals',
    exact: true
  });
  const value = totals.getByTestId('machine-count-view');
  await expect(value).toHaveText(String(count));
  await expect(page.getByTestId('location-totals-note')).toHaveCount(0);
  await value.hover();
  await expect(
    page.getByRole('tooltip', {
      name: `${descendants ? 'Descendant placements included' : 'Direct placements only'} · Location totals are independent of the loaded machine page and its search, alarm and criticality filters.`,
      exact: true
    })
  ).toBeVisible();
  await page.mouse.move(0, 0);
}

async function expectFactLabel(scope: Locator, name: string) {
  if (await scope.locator('[data-machine-card]').count()) {
    const fact = scope.locator(`td[data-label="${name}"]`).first();
    await expect(fact).toBeVisible();
    expect(
      await fact.evaluate(
        (element) => getComputedStyle(element, '::before').content
      )
    ).toBe(`"${name}"`);
  } else {
    await expect(
      scope.getByRole('columnheader', { name, exact: true })
    ).toBeVisible();
  }
}

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
  await expectLocationTotals(page, 59);
  await page
    .getByRole('button', { name: 'Choose location', exact: true })
    .click();
  if (testInfo.project.name.endsWith('phone')) {
    await expect(
      page
        .getByRole('list', { name: 'Location drill-down' })
        .getByRole('button', {
          name: 'Synthetic maintenance site',
          exact: true
        })
    ).toHaveAttribute('aria-current', 'location');
  } else
    await expect(
      page
        .getByRole('treeitem')
        .filter({ hasText: 'Synthetic maintenance site' })
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
    await expect(
      page.getByRole('checkbox', { name: 'Show inactive', exact: true })
    ).not.toBeChecked();
    await expect(
      page.getByTestId('machine-results').getByRole('checkbox')
    ).toHaveCount(0);
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
  await expect(
    page.getByTestId('machine-page-count').filter({ hasText: /^25 of 49$/ })
  ).toBeVisible();
  expect((await explorerParams(page)).has('page')).toBe(false);
  await expectLocationTotals(page, 59);
  await pagination.getByRole('button', { name: '2', exact: true }).click();
  await search.fill('S1-26');
  await expect(
    page.getByTestId('machine-page-count').filter({ hasText: /^1 machine$/ })
  ).toBeVisible();
  expect((await explorerParams(page)).has('page')).toBe(false);
  const latest = fixture.requests.at(-1)!.params;
  expect(latest.get('search')).toBe('S1-26');
  expect(latest.get('offset')).toBe('0');
  expect(latest.get('has_alarms')).toBe('true');
  await search.fill('');
  await expect(
    page.getByTestId('machine-page-count').filter({ hasText: /^25 of 49$/ })
  ).toBeVisible();
  await pagination.getByRole('button', { name: '2', exact: true }).click();
  await page
    .getByRole('switch', { name: 'Include sublocations', exact: true })
    .check();
  await expectLocationTotals(page, 79, true);
  expect((await explorerParams(page)).has('page')).toBe(false);
  expect((await explorerParams(page)).has('scope')).toBe(false);
  await expect(search).toHaveAttribute(
    'placeholder',
    'Name, serial or manufacturer'
  );
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
  await page
    .getByRole('button', { name: 'Choose location', exact: true })
    .click();
  if (testInfo.project.name.endsWith('phone')) {
    await page
      .getByRole('list', { name: 'Location drill-down' })
      .getByRole('button', { name: 'Synthetic maintenance site', exact: true })
      .click();
  } else {
    const site = page
      .getByRole('treeitem')
      .filter({ hasText: 'Synthetic maintenance site' })
      .first();
    await site.focus();
    await site.press('ArrowRight');
    await expect(
      site.getByRole('treeitem').filter({ hasText: 'Synthetic pump hall' })
    ).toBeVisible();
  }
  if (testInfo.project.name.endsWith('phone')) {
    await page
      .getByRole('button', { name: 'Synthetic pump hall', exact: true })
      .click();
  } else {
    const hall = page
      .getByRole('treeitem')
      .filter({ hasText: 'Synthetic pump hall' })
      .last();
    await hall.focus();
    await hall.press('Enter');
  }
  await expect(
    page.getByRole('button', { name: 'Choose location', exact: true })
  ).toHaveAttribute('aria-expanded', 'false');
  await expect(
    page.getByRole('heading', { name: 'Synthetic pump hall', exact: true })
  ).toBeFocused();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toContainText('20 machines');
  await expect(page.locator('[data-machine]')).toHaveCount(20);
  await showExplorerControls(page);
  await expect(
    page.getByRole('textbox', { name: 'Search machines', exact: true })
  ).toHaveValue('');
  await expect(
    page.getByRole('switch', { name: 'In alarm only', exact: true })
  ).not.toBeChecked();
  await expect(
    page.getByTestId('machine-page-count').filter({ hasText: /^20 machines$/ })
  ).toBeVisible();
  expect((await explorerParams(page)).get('location')).toBe('21');
  expect((await explorerParams(page)).has('page')).toBe(false);
  await page.getByRole('button', { name: 'Fixture back', exact: true }).click();
  await expect(
    page.getByRole('region', { name: 'Loaded page summary' })
  ).toContainText('24 of 49');
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
  ).toContainText('20 machines');
  await expect(page.locator('[data-machine]')).toHaveCount(20);
  await showExplorerControls(page);
  await expect(
    page.getByTestId('machine-page-count').filter({ hasText: /^20 machines$/ })
  ).toBeVisible();
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
    await expect(
      page.getByTestId('machine-page-count').filter({ hasText: /^24 of 49$/ })
    ).toBeVisible();
    fixture.state.status = status;
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Machines unavailable' })
    ).toBeVisible();
    await expect(page.getByTestId('machine-results')).toHaveCount(0);
    await expect(page.getByTestId('machine-page-count')).toHaveCount(0);
    await expect(page.getByText('0 machines', { exact: true })).toHaveCount(0);
    await expect(page.getByText('No machines match your search.')).toHaveCount(
      0
    );
    fixture.state.status = 200;
    await page.getByRole('button', { name: 'Retry', exact: true }).click();
    await expect(
      page.getByTestId('machine-page-count').filter({ hasText: /^24 of 49$/ })
    ).toBeVisible();
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
      .getByRole('link', { name: 'Synthetic pump 027', exact: true })
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
  await expect(
    page.getByTestId('machine-page-count').filter({ hasText: /^25 of 49$/ })
  ).toBeVisible();
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
    await expectFactLabel(table, 'Work');
    const row = (name: string) =>
      table.locator('[data-machine]').filter({ hasText: name });
    // Busy: count and the highest native priority.
    const busy = row('Synthetic pump 001');
    await expect(busy.locator('td[data-open-work="2"]')).toContainText('2');
    await expect(busy.locator('td[data-open-work="2"]')).toContainText('High');
    await expect(
      row('Synthetic pump 002').locator('td[data-open-work="1"]')
    ).toContainText('Low');
    // A failed scope read keeps its short visible label and keyboard reason.
    await expect(
      row('Synthetic pump 003').locator('td[data-open-work="unavailable"]')
    ).toHaveText('Read failed');
    const unavailableWork = row('Synthetic pump 003')
      .locator('td[data-open-work="unavailable"]')
      .getByRole('button', { name: 'Read failed', exact: true });
    await keyboardTooltip(page, unavailableWork);
    await expect(
      page.getByRole('tooltip', {
        name: 'Open work could not be checked',
        exact: true
      })
    ).toBeVisible();
    await keyboardTooltip(
      page,
      row('Synthetic pump 003').getByRole('link', {
        name: 'Synthetic pump 003',
        exact: true
      })
    );
    await expect(
      page.getByRole('tooltip', { name: unavailableMachineFacts, exact: true })
    ).toContainText('Open work could not be checked');
    const priority = busy
      .locator('td[data-open-work="2"]')
      .getByRole('link', { name: /2 open work orders · High priority/ });
    await keyboardTooltip(page, priority);
    await expect(
      page.getByRole('tooltip', {
        name: 'Highest open work priority: High',
        exact: true
      })
    ).toHaveText('Highest open work priority: High');
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
    // Phone cards preserve the same fact after their 2 × 2 layout.
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
    await expectFactLabel(table, 'Criticality');
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
    await expect(cell('Synthetic pump 003')).toHaveText('—');
    await expect(
      cell('Synthetic pump 003').getByLabel('Criticality not declared', {
        exact: true
      })
    ).toBeVisible();
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
  await expect(
    page.getByTestId('machine-page-count').filter({ hasText: /^22 machines$/ })
  ).toBeVisible();
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
  ).toContainText('25 of 109');
  await showExplorerControls(page);
  await expect(filter).toHaveValue('Any criticality');
  expect((await explorerParams(page)).has('criticality')).toBe(false);
  expect(fixture.requests.at(-1)!.params.has('criticality')).toBe(false);
});

test('each machine shows its Health condition with accessible coverage, and an unknown condition is not normal', async ({
  page
}, testInfo) => {
  await mockExplorer(page);
  for (const scheme of ['light', 'dark']) {
    await openExplorer(page, '/machines/index/machines/', scheme);
    const table = page.getByTestId('machine-results');
    await expectFactLabel(table, 'Condition');
    const cell = (name: string) =>
      table
        .locator('[data-machine]')
        .filter({ hasText: name })
        .locator('td[data-condition]');
    // Fresh source coverage does not prove all threshold assessments ran.
    await expect(cell('Synthetic pump 001')).toHaveText('Normal');
    await expect(
      cell('Synthetic pump 001').getByRole('button', {
        name: 'Normal · Assessment coverage is not supplied by this list.',
        exact: true
      })
    ).toBeVisible();
    await keyboardTooltip(
      page,
      table.getByRole('link', {
        name: 'Synthetic pump 001',
        exact: true
      })
    );
    await expect(
      page.getByRole('tooltip', {
        name: 'Assessment coverage is not supplied by this list.',
        exact: true
      })
    ).toBeVisible();
    // Limited coverage says how, apart from the state.
    await expect(cell('Synthetic pump 002')).toHaveText('Warning');
    await expect(
      cell('Synthetic pump 002').getByRole('button', {
        name: 'warning · 2 of 6 signals stale',
        exact: true
      })
    ).toBeVisible();
    await keyboardTooltip(
      page,
      table.getByRole('link', { name: 'Synthetic pump 002', exact: true })
    );
    await expect(
      page.getByRole('tooltip', { name: '2 of 6 signals stale', exact: true })
    ).toHaveText('2 of 6 signals stale');
    await expect(cell('Synthetic pump 004')).toHaveText('—');
    await expect(
      cell('Synthetic pump 004')
        .getByLabel('No signals mapped', { exact: true })
        .last()
    ).toBeVisible();
    // The integrated Health badge names absent current data explicitly.
    // Keep proving the canonical offline classification independently.
    await expect(cell('Synthetic pump 005')).toHaveAttribute(
      'data-condition',
      'offline'
    );
    await expect(cell('Synthetic pump 005')).toHaveText('No current data');
    await expect(cell('Synthetic pump 008')).toHaveText('Critical');
    await expect(
      cell('Synthetic pump 008').getByRole('button', {
        name: 'critical · Poor-quality readings',
        exact: true
      })
    ).toBeVisible();
    // Not judged reads as not available, never as a quiet normal.
    await expect(cell('Synthetic pump 003')).toHaveAttribute(
      'data-condition',
      'unavailable'
    );
    await expect(cell('Synthetic pump 003')).toHaveText('Read failed');
    await keyboardTooltip(
      page,
      table.getByRole('link', { name: 'Synthetic pump 003', exact: true })
    );
    await expect(
      page.getByRole('tooltip', { name: unavailableMachineFacts, exact: true })
    ).toContainText('Condition could not be checked');
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
    if (testInfo.project.name.endsWith('desktop'))
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

for (const scheme of ['light', 'dark'] as const) {
  test(`machine selection sits beside identity on desktop and phone with a usable focus target (${scheme})`, async ({
    page
  }, info) => {
    const fixture = await mockExplorer(page);
    fixture.state.canChange = true;
    await openExplorer(page, '/machines/index/machines/', scheme);
    const results = page.getByTestId('machine-results');
    const machine = results.locator('[data-machine="1"]');
    const select = machine.getByRole('checkbox', {
      name: 'Select Synthetic pump 001',
      exact: true
    });
    const name = machine.getByRole('link', {
      name: 'Synthetic pump 001',
      exact: true
    });
    await expect(select).toBeVisible();
    await nonText(select, machine);
    await readable(name);
    const placeholder = (
      await textSweep(
        page.getByRole('textbox', { name: 'Search machines', exact: true })
      )
    ).find((item) => item.text.startsWith('placeholder:'));
    expect(placeholder).toBeDefined();
    expect(placeholder!.ratio).toBeGreaterThanOrEqual(4.5);
    await select.focus();
    await select.press('Space');
    await expect(select).toBeChecked();
    const checkBox = (await select.boundingBox())!;
    const nameBox = (await name.boundingBox())!;
    expect(checkBox.x + checkBox.width).toBeLessThanOrEqual(nameBox.x);
    expect(nameBox.x - checkBox.x - checkBox.width).toBeLessThanOrEqual(32);
    if (info.project.name.endsWith('phone')) {
      expect(checkBox.width).toBe(20);
      expect(checkBox.height).toBe(20);
      for (const control of [
        page.getByRole('button', { name: 'Filters', exact: true }),
        page.getByRole('button', { name: 'Refresh', exact: true }),
        machine.getByRole('link', {
          name: /2 open work orders · High priority/
        })
      ]) {
        const bounds = (await control.boundingBox())!;
        expect(bounds.height).toBeGreaterThanOrEqual(44);
      }
      const hitArea = select.locator('xpath=ancestor::label');
      const hitBounds = (await hitArea.boundingBox())!;
      expect(hitBounds.width).toBeGreaterThanOrEqual(44);
      expect(hitBounds.height).toBeGreaterThanOrEqual(44);
      await hitArea.click({ position: { x: 2, y: 2 } });
      await expect(select).not.toBeChecked();
      expect(checkBox.y).toBeLessThan(nameBox.y + nameBox.height);
    } else {
      await expect(select.locator('xpath=ancestor::td')).toContainText(
        'Synthetic pump 001'
      );
      const number = machine.locator('td[data-open-work]');
      await expect(number).toHaveCSS('text-align', 'left');
      const all = page.getByRole('checkbox', {
        name: 'Select machines on this page',
        exact: true
      });
      await all.check();
      await expect(
        results.getByRole('checkbox', { checked: true })
      ).toHaveCount(26);
    }
    const missing = results
      .locator('[data-machine="3"] td[data-criticality]')
      .getByLabel('Criticality not declared', { exact: true });
    await missing.scrollIntoViewIfNeeded();
    const failedRow = results.locator('[data-machine="3"]');
    await keyboardTooltip(
      page,
      failedRow.getByRole('link', {
        name: 'Synthetic pump 003',
        exact: true
      })
    );
    const detail = page.getByRole('tooltip', {
      name: unavailableMachineFacts,
      exact: true
    });
    for (const reason of [
      'Condition could not be checked',
      'Open work could not be checked',
      'Criticality not declared'
    ])
      await expect(detail).toContainText(reason);
    // Each failed or missing fact exposes its own reason in keyboard order.
    for (const [target, reason] of [
      [
        failedRow
          .locator('td[data-condition]')
          .getByRole('button', { name: 'Read failed', exact: true }),
        'Condition could not be checked'
      ],
      [
        failedRow
          .locator('td[data-open-work]')
          .getByRole('button', { name: 'Read failed', exact: true }),
        'Open work could not be checked'
      ],
      [missing, 'Criticality not declared']
    ] as const) {
      await page.keyboard.press('Tab');
      await expect(target).toBeFocused();
      await expect(
        page.getByRole('tooltip', { name: reason, exact: true })
      ).toBeVisible();
    }
    await page.keyboard.press('Tab');
    await expect(
      results.locator('[data-machine="4"]').getByRole('checkbox', {
        name: 'Select Synthetic pump 004',
        exact: true
      })
    ).toBeFocused();
  });
}
