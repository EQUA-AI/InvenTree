import { expect, test } from '@playwright/test';

import { nativeBaseline } from '../s0/guards';
import {
  mockWorkList,
  openWorkList,
  savedWorkScope,
  workListKey
} from './workListFixtures';

nativeBaseline();

test('fifty jobs paginate on the server and open the exact owning work with native back return', async ({
  page
}, testInfo) => {
  const fixture = await mockWorkList(page);
  await openWorkList(page);
  await expect(
    page.getByText('50 work orders', { exact: false })
  ).toBeVisible();
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(26);
  expect(
    await page
      .getByRole('table')
      .getByText('Medium', { exact: true })
      .first()
      .evaluate((element) => element.scrollWidth <= element.clientWidth)
  ).toBe(true);
  await expect(
    page.getByRole('link', { name: 'WO-73', exact: true })
  ).toHaveAttribute('href', '/maintenance/work-orders/73/');
  await expect(page.getByRole('table').getByRole('button')).toHaveCount(0);
  await expect(
    page.getByRole('tab', { name: 'Risk Radar', exact: true })
  ).toHaveCount(0);
  for (const label of ['Board', 'Calendar', 'Timeline'])
    await expect(
      page.getByRole('tab', { name: label, exact: true })
    ).toBeVisible();
  await page
    .getByRole('heading', { name: 'Work list', exact: true })
    .evaluate((element) => element.scrollIntoView({ block: 'start' }));
  await page.evaluate(() => window.scrollBy(0, -80));
  await page.screenshot({
    path: testInfo.outputPath('work-list-desktop-phone.png'),
    animations: 'disabled'
  });
  const pagination = page.getByRole('navigation', {
    name: 'Work results pagination',
    exact: true
  });
  await pagination.getByRole('button', { name: '2', exact: true }).click();
  const work = page.getByRole('link', { name: 'WO-98', exact: true });
  await expect(work).toBeVisible();
  expect(fixture.requests.at(-1)!.params.get('offset')).toBe('25');
  await work.focus();
  await work.press('Enter');
  await expect(
    page.getByRole('heading', { name: 'WO-98: Synthetic job 98', exact: true })
  ).toBeVisible();
  await page.getByTestId('work-order-back').click();
  await expect(work).toBeVisible();
  await expect(
    pagination.getByRole('button', { name: '2', exact: true })
  ).toHaveAttribute('aria-current', 'page');
  expect(fixture.state.legacyReads).toBe(0);
  expect(
    fixture.requests.every(
      (request) =>
        request.method === 'GET' && request.params.get('limit') === '25'
    )
  ).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(work).toBeVisible();
  await expect(
    pagination.getByRole('button', { name: '2', exact: true })
  ).toHaveAttribute('aria-current', 'page');
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath('work-list-tablet.png'),
    animations: 'disabled'
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(work).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath('work-list-desktop-wide.png'),
    animations: 'disabled'
  });
});

test('a chosen My work scope persists only as this actor and host preference', async ({
  page
}) => {
  // Three full shell navigations share the Docker browser on a loaded host.
  // This budget leaves the scope/persistence assertions unchanged.
  test.setTimeout(90_000);
  const fixture = await mockWorkList(page);
  await openWorkList(page);
  // Keyboard choice: Needs attention now sits between the two original
  // scopes, so My work is two steps left of All work.
  const allWork = page.getByRole('radio', { name: 'All work', exact: true });
  await allWork.press('ArrowLeft');
  await page
    .getByRole('radio', { name: 'Needs attention', exact: true })
    .press('ArrowLeft');
  await expect(
    page.getByRole('radio', { name: 'My work', exact: true })
  ).toBeChecked();
  await expect(
    page.getByText('30 work orders', { exact: false })
  ).toBeVisible();
  expect(fixture.requests.at(-1)!.params.get('assigned_to')).toBe('11');
  expect(
    await page.evaluate((key) => localStorage.getItem(key), workListKey())
  ).toBe('mine');
  await page.reload();
  await expect(
    page.getByRole('radio', { name: 'My work', exact: true })
  ).toBeChecked();
  await expect(
    page.getByText('30 work orders', { exact: false })
  ).toBeVisible();
  await openWorkList(page, { actor: 12 });
  await expect(
    page.getByRole('radio', { name: 'All work', exact: true })
  ).toBeChecked();
  await expect(
    page.getByText('50 work orders', { exact: false })
  ).toBeVisible();
  expect(fixture.requests.at(-1)!.params.has('assigned_to')).toBe(false);
});

test('an explicit URL overrides saved scope without treating that scope as authority', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  await savedWorkScope(page, 'mine');
  await openWorkList(page, {
    entry: '/maintenance/work-list/?work_scope=all&assigned_to=999&work_page=2'
  });
  await expect(
    page.getByRole('radio', { name: 'All work', exact: true })
  ).toBeChecked();
  await expect(
    page.getByText('50 work orders', { exact: false })
  ).toBeVisible();
  expect(fixture.requests[0].params.has('assigned_to')).toBe(false);
  expect(fixture.requests[0].params.get('offset')).toBe('25');
  expect(
    await page.evaluate((key) => localStorage.getItem(key), workListKey())
  ).toBe('mine');
});

test('search and supported lifecycle priority and type filters reset paging and retain canonical fields', async ({
  page
}, testInfo) => {
  const fixture = await mockWorkList(page);
  await openWorkList(page, {
    entry: '/maintenance/work-list/?work_scope=all&work_page=2',
    scheme: 'dark'
  });
  if (testInfo.project.name === 's1-phone')
    await page.getByRole('button', { name: 'Filters', exact: true }).click();
  await page.getByRole('combobox', { name: 'Priority', exact: true }).click();
  await page.getByRole('option', { name: 'High', exact: true }).click();
  await expect(
    page.getByText('17 work orders', { exact: false })
  ).toBeVisible();
  expect(fixture.requests.at(-1)!.params.get('offset')).toBe('0');
  await page.getByRole('combobox', { name: 'Lifecycle', exact: true }).click();
  await page.getByRole('option', { name: 'Planned', exact: true }).click();
  await page.getByRole('combobox', { name: 'Work type', exact: true }).click();
  await page.getByRole('option', { name: 'Inspection', exact: true }).click();
  if (testInfo.project.name === 's1-phone') {
    await page.screenshot({
      path: testInfo.outputPath('work-list-phone-filters.png'),
      animations: 'disabled'
    });
    await page
      .getByRole('button', { name: 'Done filters', exact: true })
      .click();
    await expect(
      page.getByRole('button', { name: 'Filters (3)', exact: true })
    ).toBeFocused();
  }
  const search = page.getByRole('textbox', {
    name: 'Search work',
    exact: true
  });
  await search.fill('WO-73');
  await expect(page.getByText('1 work order', { exact: false })).toBeVisible();
  expect(fixture.requests.at(-1)!.params.get('search')).toBe('WO-73');
  expect(fixture.requests.at(-1)!.params.get('priority')).toBe('high');
  expect(fixture.requests.at(-1)!.params.get('lifecycle_status')).toBe(
    'planned'
  );
  expect(fixture.requests.at(-1)!.params.get('work_order_type')).toBe(
    'inspection'
  );
  await expect(
    page.getByRole('table').getByText('Planned', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('table').getByText('Synthetic owner 11', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText(/Assignment does not indicate acceptance/)
  ).toBeVisible();
  await search.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath('work-list-dark-filtered.png'),
    fullPage: true,
    animations: 'disabled'
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    )
  ).toBe(true);
});

test('a successful empty saved scope offers recovery without an authorization error', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  fixture.state.emptyMine = true;
  await savedWorkScope(page, 'mine');
  await openWorkList(page);
  await expect(
    page.getByRole('heading', { name: 'No work in this view', exact: true })
  ).toBeVisible();
  await expect(page.getByText('0 work orders', { exact: false })).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await page
    .getByRole('button', { name: 'Show all work', exact: true })
    .click();
  await expect(
    page.getByText('50 work orders', { exact: false })
  ).toBeVisible();
});

for (const status of [403, 503]) {
  test(`${status} work refresh hides cached work and retries the same page and scope`, async ({
    page
  }) => {
    const fixture = await mockWorkList(page);
    await openWorkList(page, {
      entry: '/maintenance/work-list/?work_scope=mine&work_page=2'
    });
    await expect(
      page.getByText('30 work orders', { exact: false })
    ).toBeVisible();
    fixture.state.status = status;
    await page
      .getByRole('button', { name: 'Refresh work', exact: true })
      .click();
    await expect(
      page.getByRole('alert').filter({
        hasText:
          status === 403
            ? 'Work access unavailable'
            : 'Work could not be loaded'
      })
    ).toBeVisible();
    await expect(page.getByRole('table')).toHaveCount(0);
    await expect(
      page.getByText('30 work orders', { exact: false })
    ).toHaveCount(0);
    await expect(
      page.getByRole('heading', { name: 'No work in this view', exact: true })
    ).toHaveCount(0);
    fixture.state.status = 200;
    await page.getByRole('button', { name: 'Retry work', exact: true }).click();
    await expect(
      page.getByText('30 work orders', { exact: false })
    ).toBeVisible();
    expect(fixture.requests.at(-1)!.params.get('offset')).toBe('25');
    expect(fixture.requests.at(-1)!.params.get('assigned_to')).toBe('11');
  });
}

test('an unsupported view does not issue a broader read and can recover to the saved scope', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  await savedWorkScope(page, 'mine');
  await openWorkList(page, {
    entry: '/maintenance/work-list/?work_scope=unknown'
  });
  await expect(
    page.getByRole('alert').filter({ hasText: 'Work view unavailable' })
  ).toBeVisible();
  expect(fixture.requests).toHaveLength(0);
  await page
    .getByRole('button', { name: 'Clear filters', exact: true })
    .click();
  await expect(
    page.getByText('30 work orders', { exact: false })
  ).toBeVisible();
  expect(fixture.requests[0].params.get('assigned_to')).toBe('11');
});

test('an unavailable additive provider never falls back to an unpaginated Board read', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  fixture.state.status = 404;
  await openWorkList(page);
  await expect(
    page.getByRole('alert').filter({ hasText: 'Work could not be loaded' })
  ).toBeVisible();
  expect(fixture.state.legacyReads).toBe(0);
  await expect(
    page.getByRole('heading', { name: 'No work in this view', exact: true })
  ).toHaveCount(0);
});

test('a viewer without the native work role performs no Work-list read', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  await openWorkList(page, { denied: true });
  await expect(
    page.getByRole('alert').filter({ hasText: 'Work access unavailable' })
  ).toBeVisible();
  expect(fixture.requests).toHaveLength(0);
  expect(fixture.state.personaReads).toBe(0);
});

test('an out-of-range work page reconciles against the successful scoped count', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  await openWorkList(page, {
    entry: '/maintenance/work-list/?work_scope=mine&work_page=99'
  });
  await expect(
    page.getByRole('link', { name: 'WO-98', exact: true })
  ).toBeVisible();
  await expect(
    page
      .getByRole('navigation', { name: 'Work results pagination', exact: true })
      .getByRole('button', { name: '2', exact: true })
  ).toHaveAttribute('aria-current', 'page');
  expect(
    fixture.requests.map((request) => request.params.get('offset'))
  ).toEqual(['2450', '25']);
  expect(
    fixture.requests.every(
      (request) => request.params.get('assigned_to') === '11'
    )
  ).toBe(true);
  await expect(
    page.getByRole('heading', { name: 'No work in this view', exact: true })
  ).toHaveCount(0);
});

test('selecting the native Board preserves its saved panel choice instead of forcing Work list', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  await openWorkList(page);
  await page.getByRole('tab', { name: 'Board', exact: true }).click();
  await expect(
    page.getByRole('tab', { name: 'Board', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  expect(fixture.state.legacyReads).toBeGreaterThan(0);
  await page.goto(
    `/playwright/s0-native.html?${new URLSearchParams({ surface: 'maintenance-shell', sprint: 's1', role: 'viewer', work_access: 'reader', entry: '/maintenance/' })}`
  );
  await expect(
    page.getByRole('tab', { name: 'Board', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  await expect(
    page.getByRole('tab', { name: 'Work list', exact: true })
  ).not.toHaveAttribute('aria-selected', 'true');
});

test('Needs attention asks the server for its rule, explains each row and keeps the choice', async ({
  page
}, testInfo) => {
  const fixture = await mockWorkList(page);
  await openWorkList(page);
  await page
    .getByRole('radio', { name: 'All work', exact: true })
    .press('ArrowLeft');
  await expect(
    page.getByRole('radio', { name: 'Needs attention', exact: true })
  ).toBeChecked();
  const request = fixture.requests.at(-1)!.params;
  expect(request.get('attention')).toBe('true');
  expect(request.has('assigned_to')).toBe(false);
  await expect(
    page.getByText(
      'High priority, overdue, on hold or without an owner · highest priority first, then earliest due',
      { exact: false }
    )
  ).toBeVisible();
  const row = (reference: string) =>
    page.getByRole('row').filter({
      has: page.getByRole('link', { name: reference, exact: true })
    });
  const reasons = (reference: string) =>
    row(reference).getByRole('list', { name: 'Why it needs attention' });
  await expect(reasons('WO-73')).toHaveText('High priority');
  await expect(reasons('WO-79')).toHaveText('High priorityOn hold');
  await expect(reasons('WO-75')).toHaveText('On hold');
  // A high-priority draft is not open work and does not qualify.
  await expect(
    page.getByRole('link', { name: 'WO-82', exact: true })
  ).toHaveCount(0);
  // Highest priority first: every High row precedes the first non-High row.
  const priorities = await page
    .getByRole('table')
    .locator('tbody tr td:first-child')
    .allInnerTexts();
  const firstLower = priorities.findIndex((value) => !/High/i.test(value));
  expect(firstLower).toBeGreaterThan(0);
  expect(
    priorities.slice(firstLower).some((value) => /High/i.test(value))
  ).toBe(false);
  expect(
    await page.evaluate((key) => localStorage.getItem(key), workListKey())
  ).toBe('attention');
  // The three scopes fit their panel at every width, not only the page.
  const scopes = page.getByRole('radiogroup', { name: 'Work scope' });
  const fits = await scopes.evaluate((element) => {
    const own = element.getBoundingClientRect();
    const panel = element
      .closest('.mantine-Stack-root')!
      .getBoundingClientRect();
    return {
      inside: own.left >= panel.left - 0.5 && own.right <= panel.right + 0.5,
      clipped: element.scrollWidth > element.clientWidth + 1
    };
  });
  expect(fits).toEqual({ inside: true, clipped: false });
  await page.screenshot({
    path: testInfo.outputPath('work-list-attention.png'),
    animations: 'disabled'
  });
  await row('WO-79').scrollIntoViewIfNeeded();
  await page.getByRole('table').screenshot({
    path: testInfo.outputPath('work-list-attention-rows.png'),
    animations: 'disabled'
  });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth)
  ).toBeLessThanOrEqual(page.viewportSize()!.width);

  await page.reload();
  await expect(
    page.getByRole('radio', { name: 'Needs attention', exact: true })
  ).toBeChecked();
  expect(fixture.requests.at(-1)!.params.get('attention')).toBe('true');
});

test('an empty Needs attention view says so and offers all work', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  fixture.state.emptyAttention = true;
  await openWorkList(page, {
    entry: '/maintenance/work-list/?work_scope=attention'
  });
  await expect(
    page.getByText(
      'No open work is high priority, overdue, on hold or without an owner in this view.'
    )
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Show all work', exact: true })
    .click();
  await expect(
    page.getByRole('radio', { name: 'All work', exact: true })
  ).toBeChecked();
  expect(fixture.requests.at(-1)!.params.has('attention')).toBe(false);
});

test("a supervisor's first visit starts on Needs attention without first loading all work", async ({
  page
}, testInfo) => {
  const fixture = await mockWorkList(page);
  fixture.state.persona = 'supervisor';
  let release = () => {};
  fixture.state.personaHold = new Promise((resolve) => {
    release = resolve;
  });
  await openWorkList(page, { settled: false });
  // While the starting view is unknown nothing is selected or read.
  await expect(
    page.getByRole('radiogroup', { name: 'Work scope' }).getByRole('radio', {
      checked: true
    })
  ).toHaveCount(0);
  await expect(page.getByLabel('Loading work orders')).toBeVisible();
  await expect.poll(() => fixture.state.personaReads).toBe(1);
  await expect(
    page.getByRole('button', { name: 'Refresh work', exact: true })
  ).toBeDisabled();
  await page.screenshot({
    path: testInfo.outputPath('work-list-persona-waiting.png'),
    animations: 'disabled'
  });
  // Searching while waiting narrows the starting view; it does not pick one.
  await page
    .getByRole('textbox', { name: 'Search work', exact: true })
    .fill('pump');
  await page.waitForTimeout(400);
  expect(fixture.requests).toHaveLength(0);
  release();
  await expect(
    page.getByRole('radio', { name: 'Needs attention', exact: true })
  ).toBeChecked();
  await expect(page.getByRole('table')).toBeVisible();
  expect(fixture.requests.length).toBeGreaterThan(0);
  for (const request of fixture.requests) {
    expect(request.params.get('attention')).toBe('true');
    expect(request.params.get('search')).toBe('pump');
  }
  // A starting view is not the reader's own choice: nothing (or the empty
  // "no choice" marker) is stored, so a later persona change still applies.
  expect([null, '']).toContain(
    await page.evaluate((key) => localStorage.getItem(key), workListKey())
  );
  await page.screenshot({
    path: testInfo.outputPath('work-list-persona-supervisor.png'),
    animations: 'disabled'
  });
});

test("a technician's first visit starts on My work", async ({ page }) => {
  const fixture = await mockWorkList(page);
  fixture.state.persona = 'technician';
  await openWorkList(page);
  await expect(
    page.getByRole('radio', { name: 'My work', exact: true })
  ).toBeChecked();
  await expect(
    page.getByText('Assigned to you', { exact: false })
  ).toBeVisible();
  expect(fixture.requests).toHaveLength(1);
  expect(fixture.requests[0].params.get('assigned_to')).toBe('11');
  expect(fixture.requests[0].params.has('attention')).toBe(false);
});

test('a link or a saved choice wins over the persona, which is then not read', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  fixture.state.persona = 'supervisor';
  await openWorkList(page, {
    entry: '/maintenance/work-list/?work_scope=mine'
  });
  await expect(
    page.getByRole('radio', { name: 'My work', exact: true })
  ).toBeChecked();
  await savedWorkScope(page, 'all');
  await openWorkList(page);
  await expect(
    page.getByRole('radio', { name: 'All work', exact: true })
  ).toBeChecked();
  await expect(page.getByRole('table')).toBeVisible();
  expect(fixture.state.personaReads).toBe(0);
  expect(
    fixture.requests.every((request) => !request.params.has('attention'))
  ).toBe(true);
});

test('an unavailable persona starts on the neutral all work view', async ({
  page
}) => {
  const fixture = await mockWorkList(page);
  fixture.state.personaStatus = 503;
  await openWorkList(page);
  await expect(
    page.getByRole('radio', { name: 'All work', exact: true })
  ).toBeChecked();
  await expect(
    page.getByText('50 work orders', { exact: false })
  ).toBeVisible();
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(fixture.state.personaReads).toBe(1);
  expect(fixture.requests).toHaveLength(1);
  expect(fixture.requests[0].params.has('attention')).toBe(false);
  expect(fixture.requests[0].params.has('assigned_to')).toBe(false);
});
