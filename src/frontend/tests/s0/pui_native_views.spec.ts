import { type Page, expect, test } from '@playwright/test';

import { nativeBaseline } from './guards';
import { mockWork, workOrder } from './workFixtures';

nativeBaseline();
test.use({ timezoneId: 'UTC' });

const observedAt = '2026-10-18T12:00:00Z';
const scheduledWork = [
  {
    ...workOrder(),
    title: 'Synthetic filter inspection',
    scheduled_start: '2026-10-18T08:00:00Z',
    scheduled_end: '2026-10-18T10:00:00Z'
  },
  {
    ...workOrder(86),
    title: 'Synthetic motor inspection',
    scheduled_start: '2026-10-19T09:00:00Z',
    scheduled_end: '2026-10-19T11:00:00Z'
  }
];

function riskFinding(id: number) {
  return {
    pk: id,
    scope_key: 'c1',
    rule_code: 'PACKET_STALLED',
    rule_version: 1,
    category: 'operations',
    severity: 'medium',
    severity_factors: {},
    source_model: 'repairpacket',
    source_id: id,
    title: `Synthetic finding ${id}`,
    summary: 'Synthetic condition for baseline review',
    state: 'open',
    owner: null,
    owner_username: null,
    first_seen: observedAt,
    last_seen: observedAt,
    condition_started_at: observedAt,
    source_as_of: observedAt,
    due_at: null,
    due_breached: false,
    age_hours: 1,
    snooze_until: null,
    dismiss_recheck_at: null,
    reopen_count: 0,
    version: 3
  };
}

async function mockViews(page: Page, risk = false) {
  await page.clock.setFixedTime(new Date(observedAt));
  await mockWork(page);
  await page.route('https://cdn.svar.dev/fonts/**', (route) =>
    route.fulfill({ status: 404, body: '' })
  );
  const calendarRequests: URL[] = [];
  const timelineRequests: URL[] = [];
  const riskRequests: URL[] = [];
  await page.route('**/api/aichat/ui/capabilities/', (route) =>
    route.fulfill({
      json: {
        version: 1,
        risk_radar: risk,
        command_center: false,
        maintenance_metrics: 1,
        backend_commit: 's0-synthetic'
      }
    })
  );
  await page.route('**/api/repair/risk-scopes/', (route) => {
    riskRequests.push(new URL(route.request().url()));
    return route.fulfill({
      json: { scopes: ['c1'], authorization_fingerprint: 's0-synthetic-scope' }
    });
  });
  await page.route('**/api/kanban/work-orders/?*', (route) => {
    calendarRequests.push(new URL(route.request().url()));
    return route.fulfill({ json: scheduledWork });
  });
  await page.route('**/api/kanban/schedule/**', (route) => {
    timelineRequests.push(new URL(route.request().url()));
    return route.fulfill({
      json: {
        cards: scheduledWork,
        dependencies: [
          {
            id: 1,
            from_card: 73,
            to_card: 86,
            dependency_type: 'FS',
            lag_minutes: 30
          }
        ],
        warnings: [
          {
            code: 'SYNTHETIC_OVERLAP',
            card_ids: [73, 86],
            message: 'Synthetic overlap for baseline review'
          }
        ]
      }
    });
  });
  return { calendarRequests, timelineRequests, riskRequests };
}

test('Calendar keeps its date-window read and opens the canonical work order', async ({
  page
}) => {
  const { calendarRequests } = await mockViews(page);
  await page.goto(
    '/playwright/s0-native.html?surface=maintenance&entry=/maintenance/calendar/'
  );
  await expect(
    page.getByRole('tab', { name: 'Calendar', exact: true })
  ).toBeVisible();
  const event = page
    .locator('.fc-event')
    .filter({ hasText: 'Synthetic filter inspection' })
    .first();
  await expect(event).toBeVisible();
  expect(calendarRequests.at(-1)?.searchParams.get('min_date')).toBeTruthy();
  expect(calendarRequests.at(-1)?.searchParams.get('max_date')).toBeTruthy();
  await event.click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/work-orders/73/'
  );
  await expect(
    page.getByRole('heading', { name: 'WO-73: Synthetic job 73' })
  ).toBeVisible();
});

test('Timeline preserves dependencies, overlap warnings and local search without refetch', async ({
  page
}) => {
  const { timelineRequests } = await mockViews(page);
  await page.goto(
    '/playwright/s0-native.html?surface=maintenance&entry=/maintenance/timeline/'
  );
  await expect(
    page.getByText('2 scheduled · 1 dependencies', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Scheduling conflicts', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'gantt-prev', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'gantt-today', exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'gantt-next', exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  const reads = timelineRequests.length;
  await page
    .getByRole('textbox', { name: 'gantt-search', exact: true })
    .fill('motor');
  await expect(
    page.getByText('1 scheduled · 0 dependencies', { exact: true })
  ).toBeVisible();
  expect(timelineRequests).toHaveLength(reads);
});

test('Timeline date navigation changes the bounded server window', async ({
  page
}) => {
  const { timelineRequests } = await mockViews(page);
  await page.goto(
    '/playwright/s0-native.html?surface=maintenance&entry=/maintenance/timeline/'
  );
  await expect(
    page.getByText('2 scheduled · 1 dependencies', { exact: true })
  ).toBeVisible();
  const first = timelineRequests.at(-1)?.searchParams.get('min_date');
  await page.getByRole('button', { name: 'gantt-next', exact: true }).click();
  await expect
    .poll(() => timelineRequests.at(-1)?.searchParams.get('min_date'))
    .not.toBe(first);
});

test('an explicitly disabled Risk capability hides the tab without probing scopes', async ({
  page
}) => {
  const { riskRequests } = await mockViews(page);
  await page.goto(
    '/playwright/s0-native.html?surface=maintenance&entry=/maintenance/board/'
  );
  await expect(
    page.getByRole('tab', { name: 'Board', exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('fixture-fetching')).toHaveText('0');
  await expect(
    page.getByRole('tab', { name: 'Risk Radar', exact: true })
  ).toHaveCount(0);
  expect(riskRequests).toEqual([]);
});

test('Risk Radar paginates its native server-scoped queue', async ({
  page
}) => {
  await mockViews(page, true);
  await page.route('**/api/repair/risk-findings/?*', (route) => {
    const offset = Number(
      new URL(route.request().url()).searchParams.get('offset')
    );
    return route.fulfill({
      json: {
        scope: 'c1',
        as_of: observedAt,
        source_freshness: [],
        count: 51,
        results:
          offset === 50
            ? [riskFinding(51)]
            : Array.from({ length: 50 }, (_, index) => riskFinding(index + 1))
      }
    });
  });
  await page.goto(
    '/playwright/s0-native.html?surface=maintenance&entry=/maintenance/risk-radar/'
  );
  await expect(page.getByTestId('risk-finding-row')).toHaveCount(50);
  await page
    .getByTestId('risk-radar-pagination')
    .getByText('2', { exact: true })
    .click();
  await expect(page.getByTestId('risk-finding-row')).toHaveCount(1);
  await expect(
    page.getByText('Synthetic finding 51', { exact: true })
  ).toBeVisible();
});

test('a stale Risk dismissal retains its reason and prevents blind resubmission', async ({
  page
}) => {
  await mockViews(page, true);
  await page.route('**/api/repair/risk-findings/?*', (route) =>
    route.fulfill({
      json: {
        scope: 'c1',
        as_of: observedAt,
        source_freshness: [],
        count: 1,
        results: [riskFinding(7)]
      }
    })
  );
  const commands: unknown[] = [];
  await page.route('**/api/repair/risk-findings/7/dismiss/', (route) => {
    commands.push(route.request().postDataJSON());
    return route.fulfill({
      status: 409,
      json: {
        code: 'FINDING_STATE_CONFLICT',
        detail: 'Synthetic finding changed',
        current_version: 4
      }
    });
  });
  await page.goto(
    '/playwright/s0-native.html?surface=maintenance&entry=/maintenance/risk-radar/'
  );
  await page
    .getByRole('button', { name: 'risk-finding-actions-7', exact: true })
    .click();
  await page.getByRole('menuitem', { name: 'Dismiss', exact: true }).click();
  const dialog = page.getByRole('dialog', {
    name: 'Dismiss finding',
    exact: true
  });
  await dialog
    .getByRole('textbox', { name: 'Reason', exact: false })
    .fill('Synthetic duplicate finding');
  const submit = dialog.getByRole('button', { name: 'Dismiss', exact: true });
  await submit.click();
  await expect(submit).toBeDisabled();
  await expect(
    dialog.getByRole('textbox', { name: 'Reason', exact: false })
  ).toHaveValue('Synthetic duplicate finding');
  expect(commands).toEqual([
    expect.objectContaining({
      expected_version: 3,
      reason: 'Synthetic duplicate finding'
    })
  ]);
});
