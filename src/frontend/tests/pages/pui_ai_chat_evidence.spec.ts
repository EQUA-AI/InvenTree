/**
 * S11: claim citations, coverage language, evidence sets, copy/export.
 *
 * One golden `evidence_analysis` fixture drives the live, AG-UI, reload,
 * and copy specs, so identity assertions compare like for like (Q83: the
 * live, reloaded, and exported answer carry identical scope/evidence).
 * The fixture serves its citations OUT of manifest order on purpose —
 * ordinals must come from the server, never from array position.
 */

import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import {
  type SSEEvent,
  aguiBody,
  goldenEvents,
  mockChatFoundation,
  openChat,
  sseBody,
  threadId
} from './aichat_harness.js';

const ANSWER_TEXT =
  '602 matching records were found in the current analysis scope. [1] From HX-200 Manual (revision C). [2]';

const goldenEvidenceAnalysis = {
  response_version: 2,
  response_state: 'complete',
  incomplete_reasons: [],
  no_data_reason: null,
  active_scope: { display_label: 'Solar central inverters', version: 3 },
  claims: [
    {
      claim_id: 'c1',
      claim_role: 'answer',
      claim_type: 'calculation',
      evidence_classification: 'calculated',
      citation_ordinals: [1],
      entity_refs: ['machine:1']
    },
    {
      claim_id: 'c2',
      claim_role: 'answer',
      claim_type: 'direct_source_fact',
      evidence_classification: 'documented',
      citation_ordinals: [2],
      entity_refs: []
    }
  ],
  // Served out of order: [2] first — ordinals are server truth.
  citations: [
    {
      ordinal: 2,
      source_type: 'asset_attachment',
      source_id: 'ATT-9',
      source_title: 'Site photo note',
      source_revision: null,
      source_class: 'asset_attachment',
      controlled: false,
      as_of: '2026-08-27T12:00:00+00:00',
      available: true,
      locator: { page: 4, section: null, field: null },
      applicability: null,
      evidence_set_id: 'set-golden',
      calculation: 'count: 602'
    },
    {
      ordinal: 1,
      source_type: 'work_order_population',
      source_id: 'set-golden',
      source_title: 'Work order population',
      source_revision: 'snap_abc',
      source_class: 'work_order',
      controlled: true,
      as_of: '2026-08-27T12:00:00+00:00',
      available: true,
      locator: { page: null, section: null, field: 'population' },
      applicability: null,
      evidence_set_id: 'set-golden',
      calculation: 'count: 602'
    }
  ],
  coverage: {
    population_count: 602,
    returned_count: 24,
    complete_population: true,
    display_truncated: true,
    date_field: 'created_at',
    timezone: 'UTC',
    filters: [],
    as_of: '2026-08-27T12:00:00+00:00',
    snapshot_label: 'snap_abc',
    excluded_null_date_count: null,
    incomplete_reason: null
  }
};

function evidenceEvents(
  attachment: Record<string, unknown> = goldenEvidenceAnalysis,
  answer: string = ANSWER_TEXT
): SSEEvent[] {
  return [
    goldenEvents[0],
    goldenEvents[1],
    goldenEvents[2],
    { ...goldenEvents[3], delta: answer },
    goldenEvents[4],
    {
      type: 'STATE_DELTA',
      threadId,
      runId: 'run-golden',
      kind: 'evidence_analysis',
      ...attachment
    },
    goldenEvents[5]
  ];
}

const goldenSetMembers = {
  members: [
    ...Array.from({ length: 24 }, (_, index) => ({
      member_index: index + 1,
      source_class: 'work_order',
      source_object_id: String(index + 1),
      label: `WO-${String(index + 1).padStart(4, '0')}`,
      available: true
    })),
    {
      member_index: 25,
      source_class: 'work_order',
      source_object_id: null,
      label: null,
      available: false
    }
  ],
  population_count: 602,
  complete: false,
  next_cursor: null
};

async function runEvidenceTurn(
  page: import('@playwright/test').Page,
  events: SSEEvent[] = evidenceEvents()
) {
  await page.route('**/api/ai/chat/stream', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody(events)
    });
  });
  await page.reload();
  await openChat(page);
  await page.getByPlaceholder('Type a message...').fill('how many records?');
  await page.getByLabel('send-ai-chat-message').click();
}

test('live v2 answer renders server ordinals, coverage, and control classes', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  await runEvidenceTurn(page);

  // Coverage: complete + display truncation uses the NEUTRAL wording.
  const coverage = page.getByTestId('retrieval-coverage');
  await expect(coverage).toBeVisible();
  await expect(coverage).toContainText(
    'All 602 records evaluated; showing 24 of the full result'
  );
  await expect(page.getByTestId('coverage-incomplete')).toHaveCount(0);

  // Ordinals come from the manifest, not array order: the FIRST served
  // citation is ordinal 2.
  await expect(page.getByTestId('citation-row-1').first()).toContainText(
    'Work order population'
  );
  await expect(page.getByTestId('citation-row-2').first()).toContainText(
    'Site photo note'
  );
  await expect(page.getByTestId('citation-row-2').first()).toContainText(
    'Uncontrolled attachment'
  );
  // v2 never shows model confidence.
  await expect(page.getByText('Declared confidence')).toHaveCount(0);
});

test('citation expansion reaches the evidence-set endpoint and shows members', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  const foundation = await mockChatFoundation(page, {
    evidenceSets: { 'set-golden': goldenSetMembers }
  });
  await runEvidenceTurn(page);

  await page.getByTestId('claim-evidence-toggle-1').click();
  await expect(page.getByTestId('evidence-member-1')).toContainText('WO-0001');
  // The revoked/deleted member is indistinguishable: one neutral label.
  await expect(page.getByTestId('evidence-member-25')).toContainText(
    'Not available'
  );
  await expect(page.getByTestId('evidence-set-footer')).toContainText(
    'Showing 1–25 of 602'
  );
  expect(foundation.evidenceSetReads.length).toBeGreaterThan(0);
  expect(foundation.evidenceSetReads[0].url).toContain(
    `/threads/${threadId}/evidence-sets/set-golden/members`
  );
});

test('an unavailable evidence set renders one neutral state', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page); // no evidenceSets -> 404
  await runEvidenceTurn(page);

  await page.getByTestId('claim-evidence-toggle-1').click();
  await expect(page.getByTestId('evidence-set-unavailable')).toContainText(
    'Evidence details are not available.'
  );
});

test('incomplete evaluation warns; display truncation never does', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const incomplete = {
    ...goldenEvidenceAnalysis,
    coverage: {
      ...goldenEvidenceAnalysis.coverage,
      population_count: 403,
      returned_count: 25,
      complete_population: false,
      display_truncated: false,
      incomplete_reason: null
    }
  };
  await runEvidenceTurn(page, evidenceEvents(incomplete));

  const alert = page.getByTestId('coverage-incomplete');
  await expect(alert).toBeVisible();
  await expect(alert).toContainText(
    'Incomplete coverage: 25 of 403 records evaluated'
  );
  await expect(page.getByTestId('retrieval-coverage')).toHaveCount(0);
});

test('chips render only the validated manifest — prose never links', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const events = evidenceEvents(
    goldenEvidenceAnalysis,
    `${ANSWER_TEXT} See also WO-4711 for background.`
  );
  events.splice(6, 0, {
    type: 'STATE_DELTA',
    threadId,
    runId: 'run-golden',
    kind: 'entity_manifest',
    entities: [{ model: 'assetmachine', pk: 1, label: 'Inverter 1' }]
  });
  await runEvidenceTurn(page, events);

  await expect(page.getByTestId('entity-chip-assetmachine:1')).toBeVisible();
  // The prose mention of WO-4711 never becomes a chip or a link.
  await expect(page.getByTestId('entity-chip-workorder:4711')).toHaveCount(0);
  await expect(page.getByRole('link', { name: /WO-4711/ })).toHaveCount(0);
});

test('reloaded evidence renders identically to the live turn (Q83)', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page, {
    threadDetailMessages: [
      {
        id: 'server-evidence-message',
        role: 'assistant',
        content: ANSWER_TEXT,
        timestamp: '2026-08-27T12:01:00Z',
        evidence_analysis: goldenEvidenceAnalysis
      }
    ]
  });
  // localStorage carries the SAME thread WITHOUT evidence — the server
  // projection must win and restore the full attachment.
  await page.evaluate(
    ({ durableThreadId, content }) => {
      localStorage.setItem('ai-chat-drawer-active-tab', JSON.stringify('chat'));
      localStorage.setItem(
        'ai-chat-threads',
        JSON.stringify([
          {
            id: durableThreadId,
            title: 'Server conversation',
            messages: [
              {
                id: 'server-evidence-message',
                role: 'assistant',
                content,
                timestamp: '2026-08-27T12:01:00Z'
              }
            ],
            createdAt: '2026-08-27T12:00:00Z',
            updatedAt: '2026-08-27T12:01:00Z',
            isPersisted: true
          }
        ])
      );
    },
    { durableThreadId: threadId, content: ANSWER_TEXT }
  );
  await page.reload();
  await openChat(page);

  await expect(page.getByTestId('retrieval-coverage')).toContainText(
    'All 602 records evaluated; showing 24 of the full result'
  );
  await expect(page.getByTestId('citation-row-1')).toContainText(
    'Work order population'
  );
  await expect(page.getByTestId('citation-row-2')).toContainText(
    'Uncontrolled attachment'
  );
  // Anti-duplication: exactly ONE evidence block for the message.
  await expect(page.getByTestId('claim-evidence')).toHaveCount(1);
});

test('copy carries scope, coverage and citations — identical live and reloaded', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await mockChatFoundation(page, {
    threadDetailMessages: [
      {
        id: 'server-evidence-message',
        role: 'assistant',
        content: ANSWER_TEXT,
        timestamp: '2026-08-27T12:01:00Z',
        evidence_analysis: goldenEvidenceAnalysis
      }
    ]
  });
  await runEvidenceTurn(page);
  await expect(page.getByTestId('retrieval-coverage').first()).toBeVisible();

  await page.getByLabel('copy-ai-chat-message').last().click();
  const liveCopy = await page.evaluate(() => navigator.clipboard.readText());
  expect(liveCopy).toContain('**Scope:** Solar central inverters (v3)');
  expect(liveCopy).toContain('**Coverage:** All 602 records evaluated');
  expect(liveCopy).toContain('**Limitations:** None noted');
  expect(liveCopy).toContain('[1] Work order population');
  expect(liveCopy).toContain('uncontrolled attachment');

  // Reload: the server projection carries the same attachment; the copy
  // composition is the same pure function over the same payload.
  await page.reload();
  await openChat(page);
  await expect(page.getByTestId('retrieval-coverage')).toBeVisible();
  await page.getByLabel('copy-ai-chat-message').last().click();
  const reloadCopy = await page.evaluate(() => navigator.clipboard.readText());
  expect(reloadCopy).toBe(liveCopy);
});

test('markdown export downloads exactly the copy composition', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await mockChatFoundation(page);
  await runEvidenceTurn(page);
  await expect(page.getByTestId('retrieval-coverage').first()).toBeVisible();

  await page.getByLabel('copy-ai-chat-message').last().click();
  const copyText = await page.evaluate(() => navigator.clipboard.readText());

  const downloadPromise = page.waitForEvent('download');
  await page.getByLabel('export-ai-chat-message').last().click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) {
    chunks.push(chunk as Buffer);
  }
  expect(Buffer.concat(chunks).toString('utf-8')).toBe(copyText);
  expect(download.suggestedFilename()).toMatch(/^aimms-answer-.*\.md$/);
});

test('the AG-UI wire renders the same v2 evidence', async ({ browser }) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  await page.route('**/api/ai/agui', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: aguiBody([
        { type: 'RUN_STARTED', threadId, runId: 'run-agui' },
        {
          type: 'TEXT_MESSAGE_START',
          messageId: 'message-agui',
          role: 'assistant'
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          messageId: 'message-agui',
          delta: ANSWER_TEXT
        },
        { type: 'TEXT_MESSAGE_END', messageId: 'message-agui' },
        {
          type: 'CUSTOM',
          name: 'aimms.evidenceAnalysis',
          value: goldenEvidenceAnalysis
        },
        { type: 'RUN_FINISHED', threadId, runId: 'run-agui' }
      ])
    });
  });
  await page.reload();
  await page.evaluate(() => localStorage.setItem('aimms.wire', 'agui'));
  await openChat(page);
  await page.getByPlaceholder('Type a message...').fill('how many records?');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(page.getByTestId('retrieval-coverage')).toContainText(
    'All 602 records evaluated; showing 24 of the full result'
  );
  await expect(page.getByTestId('citation-row-2')).toContainText(
    'Uncontrolled attachment'
  );
  await page.evaluate(() => localStorage.removeItem('aimms.wire'));
});

test('partial answers banner their unfinished facets; no-data states are distinct', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const partial = {
    ...goldenEvidenceAnalysis,
    response_state: 'partial',
    incomplete_reasons: [{ code: 'retrieval_timeout', facet: 'limitations' }],
    no_data_reason: null
  };
  await runEvidenceTurn(page, evidenceEvents(partial));
  const banner = page.getByTestId('analysis-partial');
  await expect(banner).toBeVisible();
  await expect(banner).toContainText('Partial answer');
  await expect(banner).toContainText('limitations');
});

test('a proven-empty population states so; the client never invents absence', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const empty = {
    ...goldenEvidenceAnalysis,
    claims: [],
    citations: [],
    no_data_reason: 'complete_population_no_matches',
    coverage: {
      ...goldenEvidenceAnalysis.coverage,
      population_count: 37,
      returned_count: 0,
      display_truncated: false
    }
  };
  await runEvidenceTurn(
    page,
    evidenceEvents(
      empty,
      'No matching records exist in the evaluated population of 37 records.'
    )
  );
  await expect(
    page.getByTestId('no-data-complete_population_no_matches')
  ).toContainText('No matching records among the 37 evaluated.');
});

test('progress stages never leak into the final answer or storage', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const events = evidenceEvents();
  events.splice(2, 0, {
    type: 'STATE_DELTA',
    threadId,
    runId: 'run-golden',
    kind: 'analysis_progress',
    stage: 'reviewing_records'
  });
  await runEvidenceTurn(page, events);

  await expect(page.getByTestId('retrieval-coverage')).toBeVisible();
  // The transient stage label is gone from the final bubble...
  await expect(page.getByTestId('analysis-progress')).toHaveCount(0);
  // ...and never persisted.
  const stored = await page.evaluate(() =>
    localStorage.getItem('ai-chat-threads')
  );
  expect(stored ?? '').not.toContain('progressStage');
  expect(stored ?? '').not.toContain('reviewing_records');
});
