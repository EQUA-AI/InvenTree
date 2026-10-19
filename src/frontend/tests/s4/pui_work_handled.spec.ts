/**
 * P18 / UI11: Handled by AIMMS on the Work panel (screens 26, 27, 63).
 *
 * Rows and counts come from the overview's `handled_actions.v1` projection and
 * nowhere else; opening the panel only reads; a server without the projection
 * is "not supplied", never "AIMMS did nothing".
 */
import { expect, test } from '@playwright/test';

import type {
  HandledAction,
  HandledActions
} from '../../lib/types/WorkExecution';

import { nativeBaseline } from '../s0/guards';
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

function action(
  id: string,
  kind: HandledAction['kind'],
  state: HandledAction['state'],
  extra: Partial<HandledAction> = {}
): HandledAction {
  return {
    id,
    kind,
    state,
    status: {
      completed: 'succeeded',
      active: 'running',
      failed: 'failed',
      unknown: 'outcome_unknown'
    }[state],
    occurred_at: '2026-10-18T11:40:00Z',
    started_at: '2026-10-18T11:38:00Z',
    finished_at: state === 'active' ? null : '2026-10-18T11:40:00Z',
    source: {
      record: 'repair_packet_generation_run',
      id: 31,
      provider: 'heuristic'
    },
    trigger: null,
    result: null,
    link: null,
    attempts: null,
    ...extra
  };
}

function projection(actions: HandledAction[]): HandledActions {
  const counts = { completed: 0, active: 0, failed: 0, unknown: 0 };
  for (const row of actions) counts[row.state] += 1;
  return {
    rule: 'handled_actions.v1',
    sources: [
      { key: 'packet_preparation', state: 'read' },
      { key: 'closeout_drafting', state: 'read' },
      { key: 'closeout_follow_up', state: 'read' }
    ],
    counts,
    actions
  };
}

const recorded: HandledAction[] = [
  action('closeout-effect:4', 'closeout_notification', 'completed', {
    occurred_at: '2026-10-18T15:10:00Z',
    status: 'succeeded',
    source: { record: 'closeout_effect', id: 4, provider: 'notification' },
    trigger: { closeout_completed_at: '2026-10-18T15:00:00Z' },
    result: { notified: 2 },
    link: { kind: 'work_panel', panel: 'closeout' },
    attempts: 1
  }),
  action('closeout-proposal:7', 'account_drafted', 'completed', {
    occurred_at: '2026-10-18T14:30:00Z',
    status: 'reviewed',
    source: { record: 'closeout_proposal', id: 7, provider: 'rules-v1' },
    result: {
      capture_revision: 2,
      fields: 5,
      part_candidates: 1,
      reading_candidates: 2
    },
    link: { kind: 'work_panel', panel: 'closeout' }
  }),
  action('packet-event:12', 'work_order_prepared', 'completed', {
    occurred_at: '2026-10-18T11:41:00Z',
    status: 'recorded',
    source: { record: 'repair_packet_event', id: 12, provider: null },
    trigger: { requested_by: 'Synthetic planner' },
    result: { reference: 'WO-73' },
    link: { kind: 'work_panel', panel: 'parts' }
  }),
  action('packet-run:31', 'packet_prepared', 'completed', {
    result: { parts: 3, gates_created: 2 },
    link: { kind: 'repair_packet', id: 901, reference: 'RP-901' }
  })
];

test('recorded AIMMS work lists saved results with counts that match the rows', async ({
  page
}, testInfo) => {
  const requests = await mockExecutionWork(page, {
    ...workOrder(),
    handled_actions: projection(recorded)
  });
  await openExecutionPanel(page);
  const work = panelRegion(page, 'Work');
  const card = work.locator('[aria-labelledby="handled-by-aimms-title"]');
  await expect(card.getByText('4 handled', { exact: true })).toBeVisible();
  await expect(card.getByTestId('handled-action')).toHaveCount(4);
  await expect(
    card.getByText('Repair packet prepared', { exact: true })
  ).toBeVisible();
  await expect(
    card.getByText(
      'Preliminary diagnosis, 3 part line(s) and 2 safety gate(s) saved',
      { exact: true }
    )
  ).toBeVisible();
  await expect(
    card.getByText('2 person(s) notified', { exact: true })
  ).toBeVisible();
  await expect(
    card.getByText(/Narrative revision 2: 5 field\(s\).*reviewed by a person/)
  ).toBeVisible();
  // No model confidence is presented as a result.
  await expect(card.getByText(/%/)).toHaveCount(0);
  await expect(
    card.getByRole('link', { name: 'Open RP-901', exact: true })
  ).toHaveAttribute('href', '/repair/packets/901/');
  await expect(
    card.getByText(
      'Checked: repair packet preparation, maintenance account drafting, closeout follow-ups.'
    )
  ).toBeVisible();
  await expectNoPageOverflow(page);
  await capturePanel(page, testInfo.outputPath('handled-recorded.png'));

  // Detail disclosure (screen 63): trigger, source, status and times.
  const created = card
    .getByTestId('handled-action')
    .filter({ hasText: 'Work order created from the repair packet' });
  await created.getByRole('button', { name: 'Details', exact: true }).click();
  await expect(
    created.getByText('Requested by Synthetic planner')
  ).toBeVisible();
  await expect(created.getByText('Repair packet event #12')).toBeVisible();
  await expect(
    created.getByText('WO-73 with its required parts list').first()
  ).toBeVisible();
  await capturePanel(page, testInfo.outputPath('handled-detail.png'));

  // A saved-result link opens the owning panel of the same Work.
  await created.getByRole('link', { name: 'Open Parts', exact: true }).click();
  await expect(
    page
      .getByRole('tablist', { name: 'Work workflow', exact: true })
      .getByRole('tab', { name: 'Parts', exact: true })
  ).toHaveAttribute('aria-selected', 'true');
  expect(onlyReads(requests)).toBe(true);
  expect(
    requests.filter((request) => request.path.endsWith('/73/overview/'))
  ).toHaveLength(1);
});

test('active, failed and unknown actions are never shown as handled', async ({
  page
}, testInfo) => {
  // Newest first, as the server orders them.
  const many = Array.from({ length: 7 }, (_, index) =>
    action(`packet-run:${100 + index}`, 'packet_prepared', 'completed', {
      occurred_at: `2026-10-1${6 - index}T08:00:00Z`,
      result: { parts: index, gates_created: 1 },
      link: { kind: 'repair_packet', id: 901, reference: 'RP-901' }
    })
  );
  const rows = [
    action('closeout-capture:5', 'account_drafting', 'active', {
      status: 'extracting',
      source: { record: 'closeout_capture', id: 5, provider: null },
      link: { kind: 'work_panel', panel: 'closeout' }
    }),
    action('closeout-effect:9', 'learning_drafted', 'unknown', {
      source: { record: 'closeout_effect', id: 9, provider: 'memory_draft' },
      attempts: 2
    }),
    action('packet-run:44', 'packet_prepared', 'failed'),
    ...many
  ];
  const requests = await mockExecutionWork(page, {
    ...workOrder(),
    handled_actions: projection(rows)
  });
  await openExecutionPanel(page, 'work', 'dark');
  const card = panelRegion(page, 'Work').locator(
    '[aria-labelledby="handled-by-aimms-title"]'
  );
  await expect(card.getByText('7 handled', { exact: true })).toBeVisible();
  await expect(card.getByText('1 in progress', { exact: true })).toBeVisible();
  await expect(
    card.getByText('1 could not complete', { exact: true })
  ).toBeVisible();
  await expect(
    card.getByText('1 outcome unknown', { exact: true })
  ).toBeVisible();
  await expect(
    card.getByText('AIMMS is handling', { exact: true })
  ).toBeVisible();
  const active = card
    .getByTestId('handled-action')
    .filter({ hasText: 'Drafting the maintenance account' });
  await expect(active.getByText('Working', { exact: true })).toBeVisible();
  const failed = card
    .getByTestId('handled-action')
    .filter({ hasText: 'Repair packet preparation' })
    .filter({ hasText: 'Could not complete' });
  await expect(
    failed.getByText('No diagnosis, parts or gates were saved by this run.')
  ).toBeVisible();
  await failed.getByRole('button', { name: 'Details', exact: true }).click();
  await expect(failed.getByText('Next owner')).toBeVisible();
  await expect(failed.getByText('None saved')).toBeVisible();
  await expect(
    card.getByText(/saved no result\. No next owner is recorded for it\./)
  ).toBeVisible();
  // Six rows first, then every recorded action on request.
  await expect(card.getByTestId('handled-action')).toHaveCount(6);
  await card
    .getByRole('button', { name: 'Show all activity (10)', exact: true })
    .click();
  await expect(card.getByTestId('handled-action')).toHaveCount(10);
  await expectNoPageOverflow(page);
  await capturePanel(page, testInfo.outputPath('handled-states-dark.png'));
  expect(onlyReads(requests)).toBe(true);
});

test('a server without the projection says the record is not supplied', async ({
  page
}) => {
  const requests = await mockExecutionWork(page, workOrder());
  await openExecutionPanel(page);
  const work = panelRegion(page, 'Work');
  await expect(
    work.getByText(
      'This server does not supply a record of AIMMS digital work for this work order. Nothing here means AIMMS did or did not act.'
    )
  ).toBeVisible();
  await expect(work.getByText(/\d+ handled/)).toHaveCount(0);
  expect(onlyReads(requests)).toBe(true);
});

test('a successful empty read names what was checked and what is not enabled', async ({
  page
}, testInfo) => {
  await mockExecutionWork(page, {
    ...workOrder(),
    handled_actions: {
      rule: 'handled_actions.v1',
      sources: [
        { key: 'packet_preparation', state: 'not_applicable' },
        { key: 'closeout_drafting', state: 'not_enabled' },
        { key: 'closeout_follow_up', state: 'not_enabled' }
      ],
      counts: { completed: 0, active: 0, failed: 0, unknown: 0 },
      actions: []
    }
  });
  await openExecutionPanel(page);
  const card = panelRegion(page, 'Work').locator(
    '[aria-labelledby="handled-by-aimms-title"]'
  );
  await expect(card.getByText('0 handled', { exact: true })).toBeVisible();
  await expect(
    card.getByText('No AIMMS record source applies to this work order.')
  ).toBeVisible();
  await expect(
    card.getByText(
      'No AIMMS record source was read. Not enabled on this server: maintenance account drafting, closeout follow-ups.'
    )
  ).toBeVisible();
  await capturePanel(page, testInfo.outputPath('handled-empty.png'));
});
