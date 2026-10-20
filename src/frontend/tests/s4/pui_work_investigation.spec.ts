import { expect, test } from '@playwright/test';
import type { InvestigationRequest } from '../../lib/types/WorkExecution';
import { sseBody } from '../pages/aichat_harness';
import { nativeBaseline } from '../s0/guards';
import {
  capturePanel,
  expectNoPageOverflow,
  mockAssistantOpen,
  mockExecutionWork,
  openExecutionPanel,
  panelRegion,
  workOrder
} from './workExecutionFixtures';

nativeBaseline();
const request = (id = 401): InvestigationRequest => ({
  id,
  work_order: 73,
  schema_version: 1,
  version: 3,
  purpose: 'Synthetic decision: distinguish bearing from guard noise',
  next_check: 'Synthetic DE envelope measurement',
  priority: 1,
  required_artifacts: [
    {
      kind: 'reading_set',
      description: 'DE envelope reading set',
      unit: 'g',
      conditions: 'Synthetic approved accessible measurement point'
    },
    {
      kind: 'laboratory',
      description: 'Named particle result',
      conditions: 'Preserve sample and method'
    }
  ],
  status: 'open',
  owner: 9,
  owner_name: 'Synthetic engineer',
  next_checkpoint: '2026-10-20T08:00:00Z',
  source_finding: null,
  source_proposal: null,
  blocked_reason: '',
  resolution: '',
  created_by: 9,
  updated_by: 9,
  updated_by_name: 'Synthetic technician',
  created_at: '2026-10-19T09:00:00Z',
  updated_at: '2026-10-19T09:05:00Z',
  artifacts: [
    {
      id: 501 + (id - 401) * 100,
      request: id,
      kind: 'laboratory',
      payload: {
        sample: 'SYN-SAMPLE-8',
        method: 'Synthetic microscopy',
        observed_at: '2026-10-19T07:00:00Z',
        source: 'Synthetic laboratory',
        status: 'pending',
        results: [{ name: 'Particles', value: null, unit: 'count/mL' }]
      },
      attachment: null,
      supersedes: null,
      recorded_by: 9,
      recorded_by_name: 'Synthetic technician',
      created_at: '2026-10-19T09:05:00Z'
    }
  ]
});
async function setup(
  page: Parameters<typeof mockExecutionWork>[0],
  rows = [request(), request(402)]
) {
  await mockExecutionWork(page, {
    ...workOrder(),
    investigation: {
      rule: 'investigation.v1',
      packet: null,
      explanation: null,
      findings: [],
      finding_history: 'not_applicable',
      intervention: null,
      requests: { available: true, reason: '', items: rows }
    }
  });
  await mockAssistantOpen(page);
  await page.route(/\/api\/approvals\/(?:\?|$)/, (route) =>
    route.fulfill({ json: { count: 0, results: [] } })
  );
}

test('saved queue preserves purpose, typed requirements and laboratory identity', async ({
  page
}, info) => {
  await setup(page);
  await openExecutionPanel(page, 'diagnose');
  const panel = panelRegion(page, 'Diagnose');
  await expect(
    panel.getByText('Priority next check · Request 401')
  ).toBeVisible();
  await expect(
    panel.getByText('Saved request queue (2 unresolved)')
  ).toBeVisible();
  const card = panel.getByTestId('investigation-request-401');
  await expect(
    card.getByText(/Decision purpose.*distinguish bearing/)
  ).toBeVisible();
  await expect(
    card.getByText(/DE envelope reading set.*g.*Synthetic approved/)
  ).toBeVisible();
  await expect(card.getByText(/Sample: SYN-SAMPLE-8/)).toBeVisible();
  await expect(card.getByText(/Method: Synthetic microscopy/)).toBeVisible();
  await expect(card.getByText(/Result status: pending/)).toBeVisible();
  await expect(
    card.getByText('Results: Particles: Not recorded', { exact: true })
  ).toBeVisible();
  await expect(card.getByText(/Not recorded count\/mL/)).toHaveCount(0);
  await expect(
    card.getByText(/Next checkpoint: 2026-10-20 11:00/)
  ).toBeVisible();
  await expect(
    card.getByText(/Saved: 2026-10-19 12:05.*Updated by: Synthetic technician/)
  ).toBeVisible();
  await expect(card.getByText(/Observed at: 2026-10-19 10:00/)).toBeVisible();
  await expect(card.getByText(/2026-10-\d\dT/)).toHaveCount(0);
  await expect(panel.locator('input,textarea,select,form')).toHaveCount(0);
  await expectNoPageOverflow(page);
  await capturePanel(page, info.outputPath('request-queue-light.png'));
  await openExecutionPanel(page, 'diagnose', 'dark');
  await expect(
    panelRegion(page, 'Diagnose').getByText(/Updated by: Synthetic technician/)
  ).toHaveCount(2);
  await expectNoPageOverflow(page);
  await capturePanel(page, info.outputPath('request-queue-dark.png'));
});

test('missing updater and checkpoint remain unrecorded while zero keeps its unit', async ({
  page
}, info) => {
  const row = request();
  row.updated_by_name = null;
  row.next_checkpoint = null;
  row.artifacts[0].payload.results = [
    { name: 'Particles', value: 0, unit: 'count/mL' }
  ];
  await setup(page, [row]);
  await openExecutionPanel(page, 'diagnose');
  const card = panelRegion(page, 'Diagnose').getByTestId(
    'investigation-request-401'
  );
  await expect(card.getByText(/Updated by: Not recorded/)).toBeVisible();
  await expect(card.getByText(/Next checkpoint: Not recorded/)).toBeVisible();
  await expect(card.getByText(/Particles: 0 count\/mL/)).toBeVisible();
  await expect(card.getByText(/Updated by: 9/)).toHaveCount(0);
  await capturePanel(page, info.outputPath('request-missing-updater.png'));
});

test('request-scoped chat acknowledges the exact revision and preserves return identity across rounds', async ({
  page
}, info) => {
  await setup(page);
  const acknowledgements: unknown[] = [];
  const sends: unknown[] = [];
  await page.route(/\/api\/ai\/threads(?:\?|$)/, (route) =>
    route.fulfill({
      json: {
        threads: [],
        shared_threads: [],
        has_more: false,
        capabilities: {
          thread_scope: true,
          work_focus: true,
          investigation_request_focus: true
        }
      }
    })
  );
  await page.route('**/api/ai/threads/*/work-focus', async (route) => {
    const body = route.request().postDataJSON();
    acknowledgements.push(body);
    const thread = decodeURIComponent(
      new URL(route.request().url()).pathname.split('/').at(-2)!
    );
    await route.fulfill({
      json: {
        schema_version: 1,
        thread_id: thread,
        focus: {
          schema_version: 2,
          kind: 'work_order',
          work_order_id: 73,
          lifecycle_version: 7,
          machine_id: 18,
          request_id: body.request_id,
          request_version: body.request_version
        },
        receipt: 'signed-request-receipt',
        expires_in: 300
      }
    });
  });
  await page.route('**/api/ai/chat/stream', async (route) => {
    const body = route.request().postDataJSON();
    sends.push(body);
    await route.fulfill({
      contentType: 'text/event-stream',
      body: sseBody([
        { type: 'RUN_STARTED', threadId: body.thread_id, runId: 'request-run' },
        {
          type: 'TEXT_MESSAGE_START',
          messageId: `response-${sends.length}`,
          role: 'assistant'
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          messageId: `response-${sends.length}`,
          delta: 'Synthetic response for saved request 401.'
        },
        { type: 'TEXT_MESSAGE_END', messageId: `response-${sends.length}` },
        { type: 'RUN_FINISHED', threadId: body.thread_id, runId: 'request-run' }
      ])
    });
  });
  await openExecutionPanel(page, 'diagnose');
  await panelRegion(page, 'Diagnose')
    .getByRole('button', { name: 'Discuss request 401', exact: true })
    .click();
  await expect(page.getByTestId('ai-chat-work-focus')).toContainText(
    'Request 401'
  );
  expect(acknowledgements).toHaveLength(0);
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('First synthetic reading set');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Synthetic response for saved request 401.', { exact: true })
  ).toBeVisible();
  expect(acknowledgements).toEqual([
    {
      work_order_id: 73,
      expected_version: 7,
      request_id: 401,
      request_version: 3
    }
  ]);
  expect((sends[0] as Record<string, unknown>).work_focus_receipt).toBe(
    'signed-request-receipt'
  );
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Second synthetic reading set');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Synthetic response for saved request 401.', { exact: true })
  ).toHaveCount(2);
  expect(acknowledgements).toHaveLength(2);
  expect(acknowledgements[1]).toEqual(acknowledgements[0]);
  expect(sends).toHaveLength(2);
  const chips = page.getByTestId('ai-chat-message-work-focus');
  await expect(chips.first()).toContainText('Request 401');
  const link = chips.first().getByRole('link');
  await expect(link).toHaveAttribute(
    'href',
    '/maintenance/work-orders/73?work_panel=diagnose&investigation_request=401'
  );
  await page.screenshot({
    path: info.outputPath('request-chat.png'),
    fullPage: true
  });
  await link.click();
  await expect(page.getByTestId('destination')).toHaveText(
    '/maintenance/work-orders/73?work_panel=diagnose&investigation_request=401'
  );
  await expect(
    panelRegion(page, 'Diagnose').getByText('Selected request 401')
  ).toBeVisible();
});

test('older server cannot silently downgrade a request to Work-only chat', async ({
  page
}) => {
  await setup(page);
  await openExecutionPanel(page, 'diagnose');
  await panelRegion(page, 'Diagnose')
    .getByRole('button', { name: 'Discuss request 401', exact: true })
    .click();
  await page
    .getByRole('textbox', { name: 'Message', exact: true })
    .fill('Keep this draft for request 401');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByRole('textbox', { name: 'Message', exact: true })
  ).toHaveValue('Keep this draft for request 401');
  await expect(
    page
      .getByRole('alert')
      .filter({ hasText: 'Investigation request not confirmed' })
  ).toBeVisible();
});

test('cannot-perform and conflicting evidence stay attributed and unresolved', async ({
  page
}, info) => {
  const row = request();
  row.status = 'cannot_perform';
  row.blocked_reason = 'Synthetic calibrated instrument unavailable';
  row.owner = null;
  row.owner_name = null;
  row.next_checkpoint = null;
  row.artifacts.push({
    ...row.artifacts[0],
    id: 502,
    kind: 'observation',
    payload: { text: 'Synthetic corrected sample label' },
    supersedes: 501
  });
  await setup(page, [row]);
  await openExecutionPanel(page, 'diagnose', 'dark');
  const card = panelRegion(page, 'Diagnose').getByTestId(
    'investigation-request-401'
  );
  await expect(
    card.getByText('Cannot perform — unresolved').first()
  ).toBeVisible();
  await expect(
    card.getByText(/Owner: Not recorded.*Next checkpoint: Not recorded/)
  ).toBeVisible();
  await expect(
    card.getByText('Synthetic calibrated instrument unavailable')
  ).toBeVisible();
  await expect(card.getByText(/Corrects evidence 501/)).toBeVisible();
  await expect(card.getByText(/Sample: SYN-SAMPLE-8/)).toBeVisible();
  await expectNoPageOverflow(page);
  await capturePanel(page, info.outputPath('cannot-perform-dark.png'));
});

test('original disputed finding and attributed correction remain inspectable', async ({
  page
}, info) => {
  const original = {
    id: 101,
    finding_key: 'SYN-F1',
    category: 'measurement',
    observation: 'Synthetic original disputed reading',
    value: 0,
    unit: 'g',
    evidence_source: 'Synthetic original instrument',
    snapshot_id: null,
    observed_at: '2026-10-19T07:00:00Z',
    verification: 'disputed',
    recorded_by: 'Synthetic first technician',
    created_at: '2026-10-19T07:00:00Z',
    updated_at: '2026-10-19T07:00:00Z'
  };
  await mockExecutionWork(page, {
    ...workOrder(),
    investigation: {
      rule: 'investigation.v1',
      packet: { id: 901, reference: 'RP-901', generation_status: 'succeeded' },
      explanation: null,
      findings: [
        {
          ...original,
          observation: 'Synthetic corrected reading',
          value: 2.4,
          verification: 'unverified',
          recorded_by: 'Synthetic second technician'
        }
      ],
      finding_history: 'preserved_revisions',
      finding_revisions: [
        {
          id: 301,
          finding_id: 101,
          snapshot: original,
          replaced_by: 'Synthetic second technician',
          replaced_at: '2026-10-19T09:00:00Z'
        }
      ],
      intervention: null,
      requests: { available: true, reason: '', items: [] }
    }
  });
  await openExecutionPanel(page, 'diagnose');
  const history = panelRegion(page, 'Diagnose').getByTestId(
    'finding-revisions'
  );
  await expect(
    history.getByText(/Synthetic original disputed reading.*0 g.*disputed/)
  ).toBeVisible();
  await expect(
    history.getByText(/Synthetic corrected reading.*2.4 g/)
  ).toBeVisible();
  await expect(
    history.getByText(
      /Synthetic first technician.*Synthetic original instrument/
    )
  ).toBeVisible();
  await expect(
    history.getByText(/Replaced by: Synthetic second technician/)
  ).toBeVisible();
  await expectNoPageOverflow(page);
  await capturePanel(page, info.outputPath('finding-correction-light.png'));
  await openExecutionPanel(page, 'diagnose', 'dark');
  const darkHistory = panelRegion(page, 'Diagnose').getByTestId(
    'finding-revisions'
  );
  await expect(
    darkHistory.getByText(/Synthetic original disputed reading.*0 g.*disputed/)
  ).toBeVisible();
  await expect(
    darkHistory.getByText(/Synthetic corrected reading.*2.4 g/)
  ).toBeVisible();
  await expectNoPageOverflow(page);
  await capturePanel(page, info.outputPath('finding-correction-dark.png'));
});

for (const scheme of ['light', 'dark'] as const) {
  test(`unprioritized requests never become a priority next check (${scheme})`, async ({
    page
  }, info) => {
    const row = request();
    row.priority = null;
    await setup(page, [row]);
    await openExecutionPanel(page, 'diagnose', scheme);
    const panel = panelRegion(page, 'Diagnose');
    await expect(panel.getByText(/Priority next check/)).toHaveCount(0);
    await expect(panel.getByText(/Priority: Not recorded/)).toBeVisible();
    await expect(
      panel.getByRole('button', { name: 'Discuss request 401' })
    ).toBeVisible();
    await expectNoPageOverflow(page);
    await capturePanel(page, info.outputPath(`request-queue-${scheme}.png`));
  });

  test(`existing chat proposal shares only after exact approval (${scheme})`, async ({
    page
  }, info) => {
    await setup(page);
    const proposal = publicationProposal();
    const acknowledgements: Record<string, unknown>[] = [];
    await page.route(/\/api\/ai\/threads(?:\?|$)/, (route) =>
      route.fulfill({
        json: {
          threads: [],
          shared_threads: [],
          has_more: false,
          capabilities: {
            thread_scope: true,
            work_focus: true,
            investigation_request_focus: true
          }
        }
      })
    );
    await page.route('**/api/ai/threads/*/work-focus', (route) => {
      const body = route.request().postDataJSON();
      acknowledgements.push(body);
      if (body.request_version !== 4)
        return route.fulfill({
          status: 400,
          json: { detail: 'Stale request revision' }
        });
      return route.fulfill({
        json: {
          schema_version: 1,
          thread_id: new URL(route.request().url()).pathname.split('/').at(-2),
          focus: {
            schema_version: 2,
            kind: 'work_order',
            work_order_id: 73,
            lifecycle_version: 7,
            machine_id: 18,
            request_id: 401,
            request_version: 4
          },
          receipt: 'new-request-receipt',
          expires_in: 300
        }
      });
    });
    await page.route('**/api/ai/chat/stream', (route) => {
      const body = route.request().postDataJSON();
      return route.fulfill({
        contentType: 'text/event-stream',
        body: sseBody([
          {
            type: 'RUN_STARTED',
            threadId: body.thread_id,
            runId: 'after-approval'
          },
          {
            type: 'TEXT_MESSAGE_START',
            messageId: 'updated-request-response',
            role: 'assistant'
          },
          {
            type: 'TEXT_MESSAGE_CONTENT',
            messageId: 'updated-request-response',
            delta: 'Continuing request at revision 4.'
          },
          { type: 'TEXT_MESSAGE_END', messageId: 'updated-request-response' },
          {
            type: 'RUN_FINISHED',
            threadId: body.thread_id,
            runId: 'after-approval'
          }
        ])
      });
    });
    let state = 'proposed';
    const writes: { path: string; body: unknown }[] = [];
    await page.route('**/api/aichat/proposals/**', async (route) => {
      if (route.request().method() === 'POST') {
        writes.push({
          path: new URL(route.request().url()).pathname,
          body: route.request().postDataJSON()
        });
        state = 'executed';
        await route.fulfill({
          json: {
            ...proposal,
            state,
            receipt: {
              command: 'investigation.artifact',
              request_id: 401,
              artifact_id: 502,
              recorded_by: 9,
              recorded_by_name: 'Synthetic technician',
              work_order_id: 73,
              request_version: 4,
              attachment_id: 801
            }
          }
        });
      } else
        await route.fulfill({
          json: {
            results: [
              {
                ...proposal,
                state,
                receipt:
                  state === 'executed'
                    ? {
                        command: 'investigation.artifact',
                        request_id: 401,
                        artifact_id: 502,
                        recorded_by: 9,
                        recorded_by_name: 'Synthetic technician',
                        work_order_id: 73,
                        request_version: 4
                      }
                    : null
              }
            ]
          }
        });
    });
    await openExecutionPanel(page, 'diagnose', scheme);
    await panelRegion(page, 'Diagnose')
      .getByRole('button', { name: 'Discuss request 401' })
      .click();
    await page.getByRole('tab', { name: 'Approvals', exact: true }).click();
    const card = page.getByTestId('chat-action-proposal');
    await expect(
      card.getByText('Publish investigation evidence: WO-73', { exact: true })
    ).toBeVisible();
    await expect(card.getByText('Request 401 · revision 3')).toBeVisible();
    await expect(
      card.getByText(/Description: Synthetic technician report/)
    ).toBeVisible();
    await expect(
      card.getByText(/Selected chat file: synthetic-report.pdf/)
    ).toBeVisible();
    await expect(
      card.getByText(/will become visible to readers of this Work/)
    ).toBeVisible();
    expect(writes).toHaveLength(0);
    await card
      .getByTestId('investigation-publication-review')
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: info.outputPath(`chat-proposal-${scheme}.png`),
      fullPage: false,
      animations: 'disabled'
    });
    await card
      .getByRole('button', { name: 'Approve and share evidence' })
      .click();
    await expect(
      card.getByText(
        /Saved evidence 502 for request 401.*Recorded by: Synthetic technician/
      )
    ).toBeVisible();
    expect(writes).toEqual([
      {
        path: `/api/aichat/proposals/${proposal.id}/confirm/`,
        body: {
          confirm_phrase: '',
          expected_preview_hash: proposal.preview_hash
        }
      }
    ]);
    await expect(
      card.getByRole('button', { name: 'Approve and share evidence' })
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await page.screenshot({
      path: info.outputPath(`chat-proposal-saved-${scheme}.png`),
      fullPage: false,
      animations: 'disabled'
    });
    await page.getByRole('tab', { name: 'Chat', exact: true }).click();
    await page
      .getByRole('textbox', { name: 'Message', exact: true })
      .fill('Continue after approval');
    await page.getByLabel('send-ai-chat-message').click();
    await expect(
      page.getByText('Continuing request at revision 4.', { exact: true })
    ).toBeVisible();
    expect(acknowledgements).toEqual([
      {
        work_order_id: 73,
        expected_version: 7,
        request_id: 401,
        request_version: 4
      }
    ]);
  });
}

function publicationProposal() {
  return {
    id: 'synthetic-investigation-proposal',
    action_type: 'investigation.artifact',
    state: 'proposed',
    work_order_id: 73,
    target_version: 7,
    preview_hash: 'synthetic-exact-preview',
    reason: 'Review technician evidence for this investigation request.',
    expires_at: '2099-10-20T12:00:00Z',
    receipt: null,
    failure_code: null,
    preview: {
      reference: 'WO-73',
      title: 'Synthetic job 73',
      request_id: 401,
      request_version: 3,
      purpose: 'Synthetic decision: distinguish bearing from guard noise',
      kind: 'report',
      payload: {
        description: 'Synthetic technician report',
        source: 'Named laboratory',
        status: 'pending'
      },
      file: {
        filename: 'synthetic-report.pdf',
        size: 128,
        sha256: 'a'.repeat(64)
      },
      publication_supported: true,
      publication:
        'This selected chat file and typed artifact will become visible to readers of this Work after you approve.',
      warning:
        'Records evidence only. The request stays unresolved until explicitly addressed.'
    }
  };
}

for (const outcome of [
  'reject',
  'revision',
  'scope',
  'expiry',
  'unsupported'
] as const) {
  test(`publication ${outcome} leaves evidence unsaved in the existing assistant`, async ({
    page
  }, info) => {
    await setup(page);
    const proposal = publicationProposal();
    if (outcome === 'unsupported') {
      proposal.preview.publication_supported = false;
      proposal.preview.publication =
        'Publication is not supported for this file type or content. Nothing will be saved; choose a supported PNG, JPEG or PDF in a fresh turn.';
    }
    let rejected = false;
    let scopeChanged = false;
    const posts: string[] = [];
    await page.route('**/api/aichat/proposals/**', async (route) => {
      if (route.request().method() === 'POST') {
        posts.push(new URL(route.request().url()).pathname);
        if (outcome === 'scope') scopeChanged = true;
        if (outcome === 'reject') {
          rejected = true;
          await route.fulfill({ json: { ...proposal, state: 'rejected' } });
        } else
          await route.fulfill({
            status:
              outcome === 'scope' ? 404 : outcome === 'expiry' ? 410 : 409,
            json: {
              error:
                outcome === 'scope'
                  ? 'PROPOSAL_NOT_FOUND'
                  : outcome === 'expiry'
                    ? 'PROPOSAL_EXPIRED'
                    : 'PROPOSAL_REVALIDATION_FAILED',
              detail:
                outcome === 'scope'
                  ? 'Proposal unavailable.'
                  : outcome === 'expiry'
                    ? 'Proposal expired.'
                    : 'Work or request revision changed. Nothing was saved; return to Diagnose for a fresh review.'
            }
          });
      } else
        await route.fulfill({
          json: {
            results: scopeChanged
              ? []
              : [{ ...proposal, state: rejected ? 'rejected' : 'proposed' }]
          }
        });
    });
    await openExecutionPanel(page, 'diagnose', 'dark');
    await panelRegion(page, 'Diagnose')
      .getByRole('button', { name: 'Discuss request 401' })
      .click();
    await page.getByRole('tab', { name: 'Approvals', exact: true }).click();
    const card = page.getByTestId('chat-action-proposal');
    if (outcome === 'unsupported') {
      await expect(
        card.getByRole('button', { name: 'Approve and share evidence' })
      ).toBeDisabled();
      await expect(
        card.getByText(/Publication is not supported/)
      ).toBeVisible();
      expect(posts).toHaveLength(0);
    } else {
      await card
        .getByRole('button', {
          name: outcome === 'reject' ? 'Dismiss' : 'Approve and share evidence'
        })
        .click();
      await expect(
        (outcome === 'scope'
          ? page.getByTestId('publication-outcome')
          : card
        ).getByText(
          outcome === 'reject'
            ? 'Nothing was shared from this proposal.'
            : outcome === 'scope' || outcome === 'expiry'
              ? /No additional evidence was shared by this attempt/
              : /Nothing was saved; return to Diagnose/
        )
      ).toBeVisible();
      if (outcome === 'scope') await expect(card).toHaveCount(0);
      expect(posts).toHaveLength(1);
    }
    await expect(card.getByText(/Saved evidence/)).toHaveCount(0);
    await expectNoPageOverflow(page);
    await page.screenshot({
      path: info.outputPath(`chat-proposal-${outcome}-dark.png`),
      fullPage: false,
      animations: 'disabled'
    });
  });
}

for (const scheme of ['light', 'dark'] as const) {
  test(`retained file and reopened blockers stay truthful (${scheme})`, async ({
    page
  }, info) => {
    const pending = request();
    pending.status = 'pending_external';
    pending.owner = null;
    pending.owner_name = null;
    pending.blocked_reason = 'Awaiting named laboratory result';
    pending.artifacts[0].payload.results = [
      {
        name: 'Particles',
        value: null,
        unit: 'count/mL',
        qualification: 'Sample handling supplied by technician'
      }
    ];
    pending.artifacts[0].attachment_snapshot = {
      id: 801,
      filename: 'original.pdf',
      link: '',
      comment: 'Original report'
    };
    const reopened = request(402);
    reopened.status = 'open';
    reopened.blocked_reason = 'Old blocker must not appear as current';
    await setup(page, [pending, reopened]);
    await openExecutionPanel(page, 'diagnose', scheme);
    const card = page.getByTestId('investigation-request-401');
    await expect(
      card.getByText('Pending external — unresolved').first()
    ).toBeVisible();
    await expect(card.getByText(/Owner: Not recorded/)).toBeVisible();
    await expect(card.getByText(/internal coordinator/)).toHaveCount(0);
    await expect(
      card.getByText(/Sample handling supplied by technician/)
    ).toBeVisible();
    await expect(
      card.getByText(/Original Work attachment 801 is no longer available/)
    ).toBeVisible();
    await expect(
      page.getByText('Old blocker must not appear as current')
    ).toHaveCount(0);
    await expectNoPageOverflow(page);
    await capturePanel(
      page,
      info.outputPath(`retained-reopened-${scheme}.png`)
    );
  });
}
