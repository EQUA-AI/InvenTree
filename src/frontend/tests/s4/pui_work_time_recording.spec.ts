import { expect, test } from '@playwright/test';
import type { TimeResult } from '../../lib/types/RecordedFacts';
import { nativeBaseline } from '../s0/guards';
import { revealWorkContext } from './workExecutionFixtures';
import {
  expectNoPageOverflow,
  mockExecutionWork,
  openExecutionPanel,
  panelRegion,
  timeAssignment,
  workOrder
} from './workExecutionFixtures';
nativeBaseline();
function timeResult(): TimeResult {
  return {
    version: 7,
    time: {
      observed_at: '2026-10-20T08:00:00Z',
      coverage: 'partial',
      recorded_seconds: 600,
      intervals: [
        {
          id: 1,
          person_id: 17,
          person: 'Synthetic technician',
          started_at: '2026-10-20T07:00:00Z',
          ended_at: '2026-10-20T07:10:00Z',
          end_action: 'pause',
          recorded_by: 'Synthetic technician',
          recorded_at: '2026-10-20T07:00:00Z',
          ended_by: 'Synthetic technician',
          ended_recorded_at: '2026-10-20T07:10:00Z',
          reason: null,
          supersedes: null,
          superseded: false
        }
      ],
      gaps: [
        {
          person: 'Synthetic technician',
          started_at: '2026-10-20T07:10:00Z',
          ended_at: '2026-10-20T08:00:00Z'
        }
      ]
    },
    downtime: []
  };
}
for (const scheme of ['light', 'dark'] as const) {
  test(`O15 recorded intervals and explicit unclassified gaps (${scheme})`, async ({
    page
  }, info) => {
    const facts = timeResult();
    const work = workOrder();
    await mockExecutionWork(page, {
      ...work,
      time_assignment: timeAssignment({ time_records: facts.time })
    });
    await openExecutionPanel(page, 'work', scheme);
    await revealWorkContext(page, 'Assignment and person-work time');
    const time = panelRegion(page, 'Work').getByTestId('recorded-work-time');
    await expect(time).toContainText(
      'Recorded person-work: 10 min at last server read · coverage partial'
    );
    await time.getByRole('button', { name: 'Unclassified gaps (1)' }).click();
    await expect(time).toContainText('Unclassified');
    await expect(time.getByRole('button', { name: 'Start work' })).toHaveCount(
      0
    );
    await expectNoPageOverflow(page);
    await time.screenshot({
      path: info.outputPath(`o15-time-${scheme}.png`),
      animations: 'disabled'
    });
  });
}
test('O15 missing totals and end attribution stay unknown despite a recorded interval', async ({
  page
}) => {
  const facts = timeResult();
  await mockExecutionWork(page, {
    ...workOrder(),
    time_assignment: timeAssignment({
      time_records: {
        ...facts.time,
        recorded_seconds: null,
        intervals: facts.time.intervals.map((row) => ({
          ...row,
          end_action: null,
          ended_by: null,
          ended_recorded_at: null
        }))
      }
    })
  });
  await openExecutionPanel(page);
  await revealWorkContext(page, 'Assignment and person-work time');
  const time = panelRegion(page, 'Work').getByTestId('recorded-work-time');
  await expect(time).toContainText(
    'Recorded person-work: not recorded · coverage unknown'
  );
  await expect(time).not.toContainText('0 min at last server read');
  await expect(time).toContainText('Ended by: Not recorded');
  await expect(time).toContainText('End record: Not recorded');
});
test('O15 an uncertain start retries the original versioned intent', async ({
  page
}) => {
  const work = workOrder();
  await mockExecutionWork(page, work);
  const commands: unknown[] = [];
  let first = true;
  await page.route('**/api/tasks/work-orders/73/time/', async (route) => {
    commands.push(route.request().postDataJSON());
    if (first) {
      first = false;
      await route.abort('failed');
      return;
    }
    await route.fulfill({
      json: { ...timeResult(), version: work.lifecycle_version + 1 }
    });
  });
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  await revealWorkContext(page, 'Assignment and person-work time');
  const time = panelRegion(page, 'Work').getByTestId('recorded-work-time');
  await time.getByRole('button', { name: 'Start work', exact: true }).click();
  await expect(time).toContainText('Original outcome remains unconfirmed');
  await expect(
    time.getByRole('button', { name: 'Start work', exact: true })
  ).toBeDisabled();
  await page.reload();
  await page.getByRole('tab', { name: 'Work', exact: true }).click();
  await expect(time).toContainText(
    'An original operation is pending in this browser.'
  );
  await time.getByRole('button', { name: 'Retry retained operation' }).click();
  await expect(time).toContainText('Record saved.');
  expect(commands).toHaveLength(2);
  expect(commands[1]).toEqual(commands[0]);
});

test('O15 supervisor closes a forgotten interval and keeps the original visible', async ({
  page
}, info) => {
  const work = workOrder();
  const facts = timeResult();
  facts.time = {
    ...facts.time,
    recorded_seconds: 3600,
    gaps: [],
    intervals: [
      {
        ...facts.time.intervals[0],
        ended_at: null,
        end_action: null,
        ended_by: null,
        ended_recorded_at: null
      }
    ]
  };
  await mockExecutionWork(page, {
    ...work,
    time_assignment: timeAssignment({ time_records: facts.time })
  });
  const sent: unknown[] = [];
  await page.route('**/api/tasks/work-orders/73/time/', async (route) => {
    const body = route.request().postDataJSON();
    sent.push(body);
    const original = facts.time.intervals[0];
    await route.fulfill({
      json: {
        ...facts,
        version: work.lifecycle_version + 1,
        time: {
          ...facts.time,
          recorded_seconds: 300,
          gaps: [
            {
              person: 'Synthetic technician',
              started_at: body.fields.ended_at,
              ended_at: facts.time.observed_at
            }
          ],
          intervals: [
            { ...original, superseded: true },
            {
              ...original,
              id: 2,
              ended_at: body.fields.ended_at,
              supersedes: 1,
              reason: body.fields.reason,
              recorded_by: 'Synthetic supervisor',
              recorded_at: facts.time.observed_at,
              ended_by: null,
              ended_recorded_at: null
            }
          ]
        }
      }
    });
  });
  await openExecutionPanel(page, 'work', 'dark', false, 'supervisor');
  await revealWorkContext(page, 'Assignment and person-work time');
  const section = panelRegion(page, 'Work').getByTestId('recorded-work-time');
  await section.getByRole('button', { name: 'Supervisor correction' }).click();
  await section
    .getByRole('combobox', { name: 'Interval to correct', exact: true })
    .click();
  await page
    .getByRole('option')
    .filter({ hasText: 'Synthetic technician' })
    .click();
  await expect(
    section.getByRole('textbox', {
      name: 'Corrected end (ISO time with zone)',
      exact: true
    })
  ).toHaveValue('');
  await expect(
    section.getByRole('button', { name: 'Save correction' })
  ).toBeDisabled();
  await section
    .getByRole('textbox', {
      name: 'Corrected end (ISO time with zone)',
      exact: true
    })
    .fill('2026-10-20T07:05:00Z');
  await section
    .getByRole('textbox', { name: 'Correction reason', exact: true })
    .fill('Supervisor checked departure record');
  await section.getByRole('button', { name: 'Save correction' }).click();
  // The mocked mutation must acknowledge before its interval facts are read.
  await expect(section.getByText('Record saved.', { exact: true })).toBeVisible(
    {
      timeout: 60_000
    }
  );
  await expect(section).toContainText('Superseded original');
  await expect(section).toContainText('End record: Supervisor correction');
  await expect(section).toContainText('Ended by: Not recorded');
  await expect(section).toContainText('Supervisor checked departure record');
  expect(sent[0]).toMatchObject({
    operation: 'correct',
    expected_version: work.lifecycle_version,
    fields: { interval_id: 1 }
  });
  await section.screenshot({
    path: info.outputPath('o15-time-correction-dark.png'),
    animations: 'disabled'
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: info.outputPath('o15-time-correction-audit-dark.png'),
    fullPage: true,
    animations: 'disabled'
  });
});

test('O15 technician start, pause, resume and stop use successive server versions', async ({
  page
}, info) => {
  const work = workOrder();
  await mockExecutionWork(page, work);
  let version = work.lifecycle_version;
  const operations: string[] = [];
  const intervals: TimeResult['time']['intervals'] = [];
  await page.route('**/api/tasks/work-orders/73/time/', async (route) => {
    const body = route.request().postDataJSON();
    expect(body.expected_version).toBe(version);
    operations.push(body.operation);
    version++;
    const observed = [
      '2026-10-20T07:00:00Z',
      '2026-10-20T07:10:00Z',
      '2026-10-20T07:20:00Z',
      '2026-10-20T07:25:00Z'
    ][operations.length - 1];
    if (body.operation === 'start')
      intervals.push({
        ...timeResult().time.intervals[0],
        id: intervals.length + 1,
        person_id: 11,
        started_at: observed,
        recorded_at: observed,
        ended_at: null,
        ended_by: null,
        ended_recorded_at: null,
        end_action: null
      });
    else
      intervals[intervals.length - 1] = {
        ...intervals[intervals.length - 1],
        ended_at: observed,
        ended_by: 'Synthetic technician',
        ended_recorded_at: observed,
        end_action: body.operation
      };
    await route.fulfill({
      json: {
        ...timeResult(),
        version,
        time: {
          ...timeResult().time,
          observed_at: observed,
          intervals: [...intervals],
          recorded_seconds: intervals.reduce(
            (sum, row) =>
              sum +
              (Date.parse(row.ended_at ?? observed) -
                Date.parse(row.started_at)) /
                1000,
            0
          ),
          gaps:
            intervals.length > 1
              ? [
                  {
                    person: 'Synthetic technician',
                    started_at: intervals[0].ended_at!,
                    ended_at: intervals[1].started_at
                  }
                ]
              : []
        }
      }
    });
  });
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  await revealWorkContext(page, 'Assignment and person-work time');
  const section = panelRegion(page, 'Work').getByTestId('recorded-work-time');
  await section
    .getByRole('button', { name: 'Start work', exact: true })
    .click();
  await expect(
    section.getByRole('button', { name: 'Pause work', exact: true })
  ).toBeEnabled();
  await expect(
    section.getByRole('button', { name: 'Start work', exact: true })
  ).toBeDisabled();
  await section.screenshot({
    path: info.outputPath('o15-time-controls-light.png'),
    animations: 'disabled'
  });
  await section
    .getByRole('button', { name: 'Pause work', exact: true })
    .click();
  await expect(
    section.getByRole('button', { name: 'Start work', exact: true })
  ).toBeEnabled();
  await section
    .getByRole('button', { name: 'Start work', exact: true })
    .click();
  await expect(
    section.getByRole('button', { name: 'Stop work', exact: true })
  ).toBeEnabled();
  await section.getByRole('button', { name: 'Stop work', exact: true }).click();
  await expect(
    section.getByRole('button', { name: 'Start work', exact: true })
  ).toBeEnabled();
  await expectNoPageOverflow(page);
  expect(operations).toEqual(['start', 'pause', 'start', 'stop']);
});

test('O15 a refused resolution keeps the original intent until a durable absence receipt', async ({
  page
}) => {
  const work = workOrder();
  await mockExecutionWork(page, work);
  const sent: unknown[] = [];
  const modes: (string | undefined)[] = [];
  await page.route('**/api/tasks/work-orders/73/time/', async (route) => {
    const body = route.request().postDataJSON();
    sent.push(body);
    modes.push(route.request().headers()['x-aimms-reconcile-only']);
    if (sent.length === 1) return route.abort('failed');
    if (sent.length === 2)
      return route.fulfill({
        status: 403,
        json: { detail: 'Access changed; earlier outcome not determined' }
      });
    if (sent.length === 3)
      return route.fulfill({
        status: 200,
        json: { detail: 'Save outcome was not supplied' }
      });
    return route.fulfill({
      json: {
        record_command_outcome: 'not_committed',
        idempotency_key: body.idempotency_key
      }
    });
  });
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  await revealWorkContext(page, 'Assignment and person-work time');
  const section = panelRegion(page, 'Work').getByTestId('recorded-work-time');
  await section
    .getByRole('button', { name: 'Start work', exact: true })
    .click();
  await expect(section).toContainText('Original outcome remains unconfirmed');
  await section
    .getByRole('button', { name: 'Retry retained operation' })
    .click();
  await expect(
    section.getByRole('button', { name: 'Start work', exact: true })
  ).toBeDisabled();
  await expect(section).toContainText('Original outcome remains unconfirmed');
  await section
    .getByRole('button', { name: 'Retry retained operation' })
    .click();
  await expect(section).toContainText('Original outcome remains unconfirmed');
  await expect(
    section.getByRole('button', { name: 'Start work', exact: true })
  ).toBeDisabled();
  await section
    .getByRole('button', { name: 'Retry retained operation' })
    .click();
  await expect(section).toContainText('Original operation was not applied.');
  await expect(
    section.getByRole('button', { name: 'Start work', exact: true })
  ).toBeEnabled();
  expect(sent).toHaveLength(4);
  expect(sent[1]).toEqual(sent[0]);
  expect(sent[2]).toEqual(sent[0]);
  expect(sent[3]).toEqual(sent[0]);
  expect(modes).toEqual([undefined, '1', '1', '1']);
});

test('O15 closed Work flags its open interval and disables clock commands', async ({
  page
}, info) => {
  const facts = timeResult();
  facts.time.open_at_closure = true;
  facts.time.closed_at = '2026-10-20T07:10:00Z';
  facts.time.intervals[0] = {
    ...facts.time.intervals[0],
    person_id: 11,
    ended_at: null
  };
  await mockExecutionWork(page, {
    ...workOrder(),
    lifecycle_status: 'completed',
    time_assignment: timeAssignment({ time_records: facts.time })
  });
  await openExecutionPanel(page, 'work', 'dark', false, 'editor');
  await revealWorkContext(page, 'Assignment and person-work time');
  const section = panelRegion(page, 'Work').getByTestId('recorded-work-time');
  await expect(section).toContainText('Work is closed with an open interval.');
  await expect(section).toContainText(
    'Open interval — requires closure review'
  );
  await expect(section).not.toContainText('Working — open interval');
  for (const name of ['Start work', 'Pause work', 'Stop work'])
    await expect(
      section.getByRole('button', { name, exact: true })
    ).toBeDisabled();
  await section.screenshot({
    path: info.outputPath('o15-review-closed-time-dark.png'),
    animations: 'disabled'
  });
});

test('O15 time controls remain reachable above a fixed phone Work dock', async ({
  page
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockExecutionWork(page, workOrder());
  await openExecutionPanel(page, 'work', 'light', false, 'editor');
  await revealWorkContext(page, 'Assignment and person-work time');
  const navigation = page.getByTestId('work-panel-navigation');
  await expect(navigation).toHaveCSS('position', 'fixed');
  const controls = panelRegion(page, 'Work').getByTestId('work-time-controls');
  // Native focus scrolling from the top of the page: the controls' own
  // scroll-margin-bottom (the measured dock clearance) must keep them above
  // the real fixed navigation, with no test-side centring.
  await page.evaluate(() => window.scrollTo(0, 0));
  const start = controls.getByRole('button', {
    name: 'Start work',
    exact: true
  });
  await expect(start).toBeEnabled();
  // Precondition: before focus the control is at or below the dock, so the
  // assertion below depends on the focus scroll and its margin.
  const before = await start.boundingBox();
  const dockBefore = await navigation.boundingBox();
  expect(before!.y + before!.height).toBeGreaterThan(dockBefore!.y);
  await start.focus();
  await expect(start).toBeFocused();
  const dock = await navigation.boundingBox();
  expect(dock).not.toBeNull();
  // Pause and Stop are disabled without an open interval and so cannot take
  // focus; they share the focused control's row and are checked in place.
  for (const name of ['Start work', 'Pause work', 'Stop work']) {
    const button = controls.getByRole('button', { name, exact: true });
    const box = await button.boundingBox();
    expect(box!.y + box!.height).toBeLessThanOrEqual(dock!.y);
    expect(
      await button.evaluate((node) => {
        const r = node.getBoundingClientRect();
        const hit = document.elementFromPoint(
          r.x + r.width / 2,
          r.y + r.height / 2
        );
        return hit === node || node.contains(hit);
      })
    ).toBe(true);
  }
  await expectNoPageOverflow(page);
  await page.screenshot({
    path: info.outputPath('o15-review-time-dock-light.png'),
    animations: 'disabled'
  });
});
