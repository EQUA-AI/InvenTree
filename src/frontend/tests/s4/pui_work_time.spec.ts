import { expect, test } from '@playwright/test';
import type { TimeAssignment } from '../../lib/types/WorkExecution';
import { nativeBaseline } from '../s0/guards';
import { revealWorkContext } from './workExecutionFixtures';
import {
  capturePanel,
  expectNoPageOverflow,
  mockExecutionWork,
  onlyReads,
  openExecutionPanel,
  panelRegion,
  workOrder
} from './workExecutionFixtures';

nativeBaseline();
function assignment(extra: Partial<TimeAssignment> = {}): TimeAssignment {
  return {
    rule: 'time_assignment.v1',
    observed_at: '2026-10-18T13:00:00Z',
    assigned_to: 'Synthetic current assignee',
    legacy_assignee: null,
    assignment_event_at: '2026-10-18T12:00:00Z',
    elapsed_assignment_seconds: 3600,
    assignment_reason: 'Synthetic recorded specialist allocation',
    assignment_recorded_by: 'Synthetic planner',
    events: [
      {
        id: 41,
        recorded_by: 'Synthetic planner',
        recorded_at: '2026-10-18T12:00:00Z',
        reason: 'Synthetic recorded specialist allocation',
        assigned_to_id: 17,
        previous_assigned_to_id: 16,
        assigned_to_name: 'Synthetic current assignee',
        previous_assigned_to_name: 'Synthetic previous assignee'
      }
    ],
    attributed_intervals: { available: false, reason: 'no_provider' },
    coverage: 'unknown',
    correction: { available: false, reason: 'no_provider' },
    assignment_factors: { available: false, reason: 'no_provider' },
    accepted_handover: { available: false, reason: 'no_provider' },
    ...extra
  };
}
for (const scheme of ['light', 'dark'] as const) {
  test(`assignment elapsed is separate from unknown person-work time on held work (${scheme})`, async ({
    page
  }, info) => {
    const requests = await mockExecutionWork(page, {
      ...workOrder(),
      lifecycle_status: 'on_hold',
      assigned_to: 17,
      assigned_to_name: 'Synthetic current assignee',
      assigned_to_username: 'synthetic-assignee',
      time_assignment: assignment()
    });
    await openExecutionPanel(page, 'work', scheme);
    await revealWorkContext(page, 'Assignment and person-work time');
    const time = panelRegion(page, 'Work').getByTestId('time-assignment');
    await expect(
      time.getByText('60 min at last read', { exact: true })
    ).toBeVisible();
    await expect(
      time
        .getByText('Synthetic recorded specialist allocation', {
          exact: true
        })
        .filter({ visible: true })
    ).toBeVisible();
    await expect(
      time.getByText('Attributed time: unavailable · coverage unknown')
    ).toBeVisible();
    await expect(
      time.getByText(/Held work does not mean every person was waiting/)
    ).toBeVisible();
    await time
      .getByRole('button', { name: 'Recorded assignment changes (1)' })
      .click();
    await expect(
      time.getByText(
        'Assignment changes are audit records, not accepted handovers or attributed work intervals.'
      )
    ).toBeVisible();
    await expect(
      time.getByText(
        'Assignee recorded in this event: Synthetic current assignee',
        { exact: true }
      )
    ).toBeVisible();
    await expect(
      time.getByText(
        'Previous assignee recorded in this event: Synthetic previous assignee',
        { exact: true }
      )
    ).toBeVisible();
    await expect(time.getByText('Native assignee record')).toHaveCount(0);
    await expect(
      time.getByRole('button', {
        name: /Start time|Working|Waiting|Away|Accept|Correct time/
      })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`time-assignment-${scheme}.png`));
    expect(onlyReads(requests)).toBe(true);
  });
}
test('missing audit names stay unknown instead of exposing raw assignee ids', async ({
  page
}, info) => {
  const value = assignment({
    assigned_to: null,
    assignment_event_at: null,
    elapsed_assignment_seconds: null,
    assignment_reason: null,
    assignment_recorded_by: null
  });
  value.events[0].assigned_to_name = null;
  value.events[0].previous_assigned_to_name = null;
  await mockExecutionWork(page, { ...workOrder(), time_assignment: value });
  await openExecutionPanel(page);
  await revealWorkContext(page, 'Assignment and person-work time');
  const time = page.getByTestId('time-assignment');
  await time
    .getByRole('button', { name: 'Recorded assignment changes (1)' })
    .click();
  await expect(
    time.getByText('Assignee recorded in this event: Not recorded', {
      exact: true
    })
  ).toBeVisible();
  await expect(
    time.getByText('Previous assignee recorded in this event: Not recorded', {
      exact: true
    })
  ).toBeVisible();
  await expect(
    time.getByText(/Native assignee record|Unassigned in this event/)
  ).toHaveCount(0);
  await capturePanel(
    page,
    info.outputPath('time-assignment-missing-names.png')
  );
});
test('legacy or missing assignment provenance stays unknown', async ({
  page
}, info) => {
  await mockExecutionWork(page, {
    ...workOrder(),
    assigned_to: null,
    assigned_to_name: null,
    assigned_to_username: null,
    assignee: 'Synthetic legacy label',
    time_assignment: assignment({
      assigned_to: null,
      legacy_assignee: 'Synthetic legacy label',
      assignment_event_at: null,
      elapsed_assignment_seconds: null,
      assignment_reason: null,
      assignment_recorded_by: null,
      events: []
    })
  });
  await openExecutionPanel(page);
  await revealWorkContext(page, 'Assignment and person-work time');
  const time = page.getByTestId('time-assignment');
  await expect(
    time.getByText('Synthetic legacy label', { exact: true })
  ).toBeVisible();
  await expect(time.getByText(/\d+ min at last read/)).toHaveCount(0);
  await time
    .getByRole('button', { name: 'Recorded assignment changes (0)' })
    .click();
  await expect(
    time.getByText('No assignment changes recorded', { exact: true })
  ).toBeVisible();
  await capturePanel(page, info.outputPath('time-legacy-unknown.png'));
  await mockExecutionWork(page);
  await page.reload();
  await revealWorkContext(page, 'Assignment and person-work time');
  await expect(
    page
      .getByTestId('time-assignment')
      .getByText('Attributed time: unavailable · coverage unknown')
  ).toBeVisible();
});
