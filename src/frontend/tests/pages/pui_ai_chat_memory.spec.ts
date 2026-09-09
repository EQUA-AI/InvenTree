/**
 * M2 PR 9: "What this chat remembers" (plan 8.6 items 3-4) and the
 * "Context used" disclosure (plan 9.11), both under GR-16.
 *
 * Privacy discipline pinned here:
 * - the memory affordance appears on owned, persisted rows only — never on
 *   a shared (read-only) row;
 * - the modal is the ONLY place a summary body renders, and it labels the
 *   content as a summary, not verified data;
 * - corrections are credentialed, CSRF-bearing PUTs carrying exactly
 *   {item_id, action} — no idempotency key — and refetch afterwards;
 * - a 404 paints "not available" and never a server error body;
 * - the memory affordance is also absent on a non-persisted (local-only)
 *   row, and a correction whose PUT resolves after the modal closed never
 *   repaints, so a re-open for another thread starts from an empty body;
 * - the Context used disclosure renders ids and counts only, even when a
 *   text field rides along in the record — on the persisted reload
 *   projection, the AG-UI `aimms.contextUsed` channel and the legacy
 *   STATE_DELTA frame alike; the server's closed-enum sentinels
 *   (`degrade_reason: 'none'`, corpus `consulted_none`) paint as the plan's
 *   labels, never as raw codes.
 */

import type { Page, Route } from '@playwright/test';

import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
import {
  type ObservedRequest,
  aguiBody,
  expectCredentialedUnsafeRequest,
  goldenEvents,
  mockChatFoundation,
  observeRequest,
  openChat,
  seedLegacyHistory,
  sseBody,
  threadId
} from './aichat_harness.js';

const sharedThreadId = 'shared-thread-1';
/** A second owned, persisted row (older than the golden one, so never active). */
const secondOwnedThreadId = 'owned-thread-2';

const memoryFixture = {
  thread_id: threadId,
  label: 'Pump 7 vibration follow-up',
  through_sequence: 12,
  latest_sequence: 20,
  body_version: 2,
  open_questions: [
    {
      id: 'oq1',
      text: 'Was the bearing replaced in July?',
      lifecycle: 'active',
      memory_type: 'open_question',
      created_seq: 4,
      superseded_by: null,
      directive_flags: []
    },
    {
      id: 'oq2',
      text: 'Forgotten question body',
      lifecycle: 'forgotten',
      memory_type: 'open_question',
      created_seq: 2,
      superseded_by: null,
      directive_flags: []
    }
  ],
  pending_proposals: [],
  machine_facts: [
    {
      id: 'mf1',
      text: 'Pump 7 runs at 1450 rpm',
      lifecycle: 'active',
      memory_type: 'machine_fact',
      created_seq: 6,
      superseded_by: null,
      directive_flags: []
    },
    {
      id: 'mf2',
      text: 'Pump 7 runs at 1200 rpm',
      lifecycle: 'superseded',
      memory_type: 'machine_fact',
      created_seq: 3,
      superseded_by: 'mf1',
      directive_flags: []
    }
  ],
  corrections: [
    {
      id: 'co1',
      text: 'Speed corrected from 1200 to 1450 rpm',
      lifecycle: 'active',
      memory_type: 'correction',
      created_seq: 6,
      superseded_by: null,
      directive_flags: []
    }
  ],
  citation_keys: ['doc:HX-200:C'],
  narrative: 'Narrative paragraph of the compacted summary.',
  exclusions_count: 1
};

type MemoryFixture = typeof memoryFixture;

/** A manual promise the route handlers can be told to wait on. */
interface Gate {
  wait: Promise<void>;
  release: () => void;
}

function gate(): Gate {
  let release: () => void = () => {};
  const wait = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { wait, release };
}

interface MemoryObservations {
  memoryReads: ObservedRequest[];
  corrections: ObservedRequest[];
  /** Mutable: the GET response served next (fixture, or a 404). */
  serve: MemoryFixture | 'not_found';
  /** Mutable: the status the next correction PUT answers with. */
  correctionStatus: number;
  /** Mutable: when set, memory GETs are observed, then held until released. */
  memoryReadGate: Gate | null;
  /** Mutable: when set, correction PUTs are observed, then held until released. */
  correctionGate: Gate | null;
}

function threadRow(
  id: string,
  title: string,
  shared: boolean,
  day = '2026-07-15'
) {
  return {
    thread_id: id,
    title,
    message_count: 1,
    turn_count: 1,
    summary: '',
    created_at: `${day}T00:00:00Z`,
    last_activity: `${day}T00:01:00Z`,
    is_persisted: true,
    shared,
    active_scope: {
      mode: 'legacy_unconfirmed',
      version: 0,
      display_label: ''
    }
  };
}

/**
 * Layered over mockChatFoundation (registered later, so it wins): serves
 * the memory endpoints and a thread list carrying one shared row; every
 * other /threads request falls back to the foundation mock.
 */
async function mockMemoryRoutes(page: Page): Promise<MemoryObservations> {
  const observations: MemoryObservations = {
    memoryReads: [],
    corrections: [],
    serve: memoryFixture,
    correctionStatus: 200,
    memoryReadGate: null,
    correctionGate: null
  };

  await page.route('**/api/ai/threads**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());

    if (/\/threads\/[^/]+\/memory\/corrections$/.test(url.pathname)) {
      const observed = await observeRequest(request);
      observations.corrections.push(observed);
      if (observations.correctionGate) {
        await observations.correctionGate.wait;
      }
      if (observations.correctionStatus !== 200) {
        await route.fulfill({
          status: observations.correctionStatus,
          json: { detail: 'memory_version_conflict' }
        });
        return;
      }
      await route.fulfill({
        json: {
          thread_id: threadId,
          item_id: observed.body?.item_id,
          result: 'applied',
          through_sequence: 12
        }
      });
      return;
    }

    if (/\/threads\/[^/]+\/memory$/.test(url.pathname)) {
      observations.memoryReads.push(await observeRequest(request));
      if (observations.memoryReadGate) {
        await observations.memoryReadGate.wait;
      }
      if (observations.serve === 'not_found') {
        await route.fulfill({
          status: 404,
          json: { detail: 'Thread not found' }
        });
        return;
      }
      await route.fulfill({ json: observations.serve });
      return;
    }

    if (url.pathname.endsWith('/threads') && request.method() === 'GET') {
      await route.fulfill({
        json: {
          threads: [
            threadRow(threadId, 'Server conversation', false),
            threadRow(
              secondOwnedThreadId,
              'Second conversation',
              false,
              '2026-07-10'
            )
          ],
          shared_threads: [
            threadRow(sharedThreadId, 'Shared conversation', true)
          ],
          sync_token: null,
          has_more: false,
          capabilities: { thread_scope: true }
        }
      });
      return;
    }

    await route.fallback();
  });

  return observations;
}

/**
 * Opens the modal and returns its dialog. Mantine's `Modal-root` wrapper
 * (the element carrying the aria-label / testid) has no box of its own,
 * so visibility is asserted on the dialog role it contains.
 */
async function openMemoryModal(page: Page) {
  await page.getByLabel('select-ai-chat-thread').click();
  await page.getByLabel(`memory-ai-chat-thread-${threadId}`).click();
  await expect(page.getByLabel('thread-memory-modal')).toHaveCount(1);
  const modal = page.getByRole('dialog', { name: 'What this chat remembers' });
  await expect(modal).toBeVisible();
  return modal;
}

test('memory affordance appears on owned persisted rows only, never on shared or local-only rows', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  await mockMemoryRoutes(page);
  // A legacy local-only thread (no server row, no `isPersisted`) survives
  // the server merge and must NOT get the affordance either.
  await seedLegacyHistory(page);

  await page.reload();
  await openChat(page);
  await page.getByLabel('select-ai-chat-thread').click();

  await expect(
    page.getByLabel(`memory-ai-chat-thread-${threadId}`)
  ).toBeVisible();
  await expect(
    page.getByLabel(`memory-ai-chat-thread-${secondOwnedThreadId}`)
  ).toBeVisible();

  await expect(
    page.getByTestId(`shared-thread-${sharedThreadId}`)
  ).toBeVisible();
  await expect(
    page.getByLabel(`memory-ai-chat-thread-${sharedThreadId}`)
  ).toHaveCount(0);
  await expect(
    page.getByLabel(`delete-ai-chat-thread-${sharedThreadId}`)
  ).toHaveCount(0);

  await expect(
    page.getByText('Legacy local conversation', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByLabel('memory-ai-chat-thread-legacy-local-only')
  ).toHaveCount(0);
  // The row is otherwise a normal owned row (rename/delete stay).
  await expect(
    page.getByLabel('delete-ai-chat-thread-legacy-local-only')
  ).toBeVisible();
});

test('memory modal fetches the body and renders it as a summary, not verified data', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const memory = await mockMemoryRoutes(page);

  await page.reload();
  await openChat(page);
  const modal = await openMemoryModal(page);

  await expect.poll(() => memory.memoryReads.length).toBe(1);
  const read = memory.memoryReads[0];
  expect(read.method).toBe('GET');
  expect(new URL(read.url).pathname).toBe(`/api/ai/threads/${threadId}/memory`);
  expect(read.headers.cookie).toBeTruthy();
  expect(read.headers['x-user-id']).toBeUndefined();

  await expect(modal.getByTestId('thread-memory-label')).toHaveText(
    'Pump 7 vibration follow-up'
  );
  await expect(modal.getByTestId('thread-memory-watermark')).toHaveText(
    'Through message 12 of 20'
  );
  await expect(modal.getByTestId('thread-memory-disclaimer')).toContainText(
    'Summary, not verified data'
  );

  // Active items: text plus both verbs.
  await expect(modal.getByTestId('memory-item-mf1')).toContainText(
    'Pump 7 runs at 1450 rpm'
  );
  await expect(page.getByLabel('memory-item-wrong-mf1')).toBeVisible();
  await expect(page.getByLabel('memory-item-forget-mf1')).toBeVisible();
  await expect(page.getByLabel('memory-item-wrong-oq1')).toBeVisible();
  await expect(page.getByLabel('memory-item-forget-co1')).toBeVisible();

  // Inactive items: muted with a lifecycle badge and NO verbs.
  await expect(modal.getByTestId('memory-item-mf2')).toContainText(
    'superseded'
  );
  await expect(page.getByLabel('memory-item-wrong-mf2')).toHaveCount(0);
  await expect(page.getByLabel('memory-item-forget-mf2')).toHaveCount(0);
  await expect(modal.getByTestId('memory-item-oq2')).toContainText('forgotten');
  await expect(page.getByLabel('memory-item-wrong-oq2')).toHaveCount(0);
  await expect(page.getByLabel('memory-item-forget-oq2')).toHaveCount(0);

  await expect(
    modal.getByTestId('memory-section-pending_proposals')
  ).toContainText('None');
  await expect(modal.getByTestId('thread-memory-citations')).toContainText(
    'doc:HX-200:C'
  );
  await expect(modal.getByTestId('thread-memory-narrative')).toContainText(
    'Narrative paragraph of the compacted summary.'
  );
  await expect(modal.getByTestId('thread-memory-exclusions')).toHaveText(
    '1 forgotten'
  );
});

test('forget PUTs a credentialed correction without an idempotency key and refetches', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const memory = await mockMemoryRoutes(page);

  await page.reload();
  await openChat(page);
  const modal = await openMemoryModal(page);
  await expect.poll(() => memory.memoryReads.length).toBe(1);
  await expect(page.getByLabel('memory-item-forget-mf1')).toBeVisible();

  // After the write the server reports mf1 as forgotten.
  memory.serve = {
    ...memoryFixture,
    machine_facts: memoryFixture.machine_facts.map((item) =>
      item.id === 'mf1' ? { ...item, lifecycle: 'forgotten' } : item
    ),
    exclusions_count: 2
  };

  await page.getByLabel('memory-item-forget-mf1').click();

  await expect.poll(() => memory.corrections.length).toBe(1);
  const correction = memory.corrections[0];
  expect(correction.method).toBe('PUT');
  expect(new URL(correction.url).pathname).toBe(
    `/api/ai/threads/${threadId}/memory/corrections`
  );
  expectCredentialedUnsafeRequest(correction);
  expect(correction.body).toEqual({ item_id: 'mf1', action: 'forget' });
  expect(correction.body).not.toHaveProperty('idempotency_key');
  expect(correction.headers['idempotency-key']).toBeUndefined();

  // Refetch: the forgotten item loses its verbs; the count updates.
  await expect.poll(() => memory.memoryReads.length).toBe(2);
  await expect(page.getByLabel('memory-item-forget-mf1')).toHaveCount(0);
  await expect(page.getByLabel('memory-item-wrong-mf1')).toHaveCount(0);
  await expect(modal.getByTestId('memory-item-mf1')).toContainText('forgotten');
  await expect(modal.getByTestId('thread-memory-exclusions')).toHaveText(
    '2 forgotten'
  );

  // "This is wrong" carries the other action code on the same route.
  await page.getByLabel('memory-item-wrong-oq1').click();
  await expect.poll(() => memory.corrections.length).toBe(2);
  expect(memory.corrections[1].body).toEqual({
    item_id: 'oq1',
    action: 'wrong'
  });
  await expect.poll(() => memory.memoryReads.length).toBe(3);
});

test('a 409 on a correction refetches and asks to try again', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const memory = await mockMemoryRoutes(page);

  await page.reload();
  await openChat(page);
  await openMemoryModal(page);
  await expect.poll(() => memory.memoryReads.length).toBe(1);

  memory.correctionStatus = 409;
  await page.getByLabel('memory-item-forget-mf1').click();
  await expect.poll(() => memory.corrections.length).toBe(1);
  await expect.poll(() => memory.memoryReads.length).toBe(2);
  await expect(page.getByTestId('thread-memory-notice')).toContainText(
    'Memory changed, try again'
  );
  // The server's detail string never paints.
  await expect(page.getByText('memory_version_conflict')).toHaveCount(0);
  // The body is still there for a second attempt.
  await expect(page.getByLabel('memory-item-forget-mf1')).toBeVisible();
});

test('closing the modal during an in-flight correction never repaints; a re-open for another thread starts empty', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const memory = await mockMemoryRoutes(page);

  await page.reload();
  await openChat(page);
  const modal = await openMemoryModal(page);
  await expect.poll(() => memory.memoryReads.length).toBe(1);
  await expect(modal.getByTestId('thread-memory-label')).toHaveText(
    'Pump 7 vibration follow-up'
  );

  // Hold the PUT, then close the modal while it is still in flight.
  const correction = gate();
  memory.correctionGate = correction;
  await page.getByLabel('memory-item-forget-mf1').click();
  await expect.poll(() => memory.corrections.length).toBe(1);
  // The modal's own close button (the drawer also listens for Escape).
  await page.getByLabel('thread-memory-close').click();
  await expect(modal).toBeHidden();

  // If anything refetched for the closed modal, this is what it would get.
  memory.serve = { ...memoryFixture, label: 'STALE-AFTER-CLOSE' };
  memory.correctionGate = null;
  correction.release();
  // Give a (wrong) post-close refetch every chance to land before moving on.
  await page.waitForTimeout(500);
  expect(memory.memoryReads.length).toBe(1);

  // Re-open for ANOTHER owned thread with its GET held: nothing from the
  // first thread may be on screen while the second one loads.
  const read = gate();
  memory.memoryReadGate = read;
  memory.serve = {
    ...memoryFixture,
    thread_id: secondOwnedThreadId,
    label: 'Second conversation memory',
    narrative: 'Second narrative.',
    machine_facts: [],
    corrections: [],
    open_questions: [],
    citation_keys: [],
    exclusions_count: 0
  };
  const secondIcon = page.getByLabel(
    `memory-ai-chat-thread-${secondOwnedThreadId}`
  );
  if (!(await secondIcon.isVisible())) {
    await page.getByLabel('select-ai-chat-thread').click();
  }
  await secondIcon.click();
  const reopened = page.getByRole('dialog', {
    name: 'What this chat remembers'
  });
  await expect(reopened).toBeVisible();
  await expect.poll(() => memory.memoryReads.length).toBe(2);
  expect(new URL(memory.memoryReads[1].url).pathname).toBe(
    `/api/ai/threads/${secondOwnedThreadId}/memory`
  );
  await expect(reopened.getByTestId('thread-memory-label')).toHaveCount(0);
  await expect(reopened.getByTestId('thread-memory-narrative')).toHaveCount(0);
  await expect(reopened.getByTestId('memory-item-mf1')).toHaveCount(0);
  await expect(page.getByText('Pump 7', { exact: false })).toHaveCount(0);
  await expect(page.getByText('STALE-AFTER-CLOSE')).toHaveCount(0);

  memory.memoryReadGate = null;
  read.release();
  await expect(reopened.getByTestId('thread-memory-label')).toHaveText(
    'Second conversation memory'
  );
  await expect(reopened.getByTestId('thread-memory-narrative')).toContainText(
    'Second narrative.'
  );
  await expect(page.getByText('Pump 7', { exact: false })).toHaveCount(0);
  await expect(page.getByText('STALE-AFTER-CLOSE')).toHaveCount(0);
});

test('a 404 on the memory route shows the not-available state and no body', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  const memory = await mockMemoryRoutes(page);
  memory.serve = 'not_found';

  await page.reload();
  await openChat(page);
  const modal = await openMemoryModal(page);

  await expect.poll(() => memory.memoryReads.length).toBe(1);
  await expect(modal.getByTestId('thread-memory-unavailable')).toContainText(
    'Memory is not available'
  );
  await expect(modal.getByTestId('thread-memory-label')).toHaveCount(0);
  await expect(modal.getByTestId('thread-memory-watermark')).toHaveCount(0);
  await expect(modal.getByTestId('thread-memory-narrative')).toHaveCount(0);
  await expect(page.getByText('Thread not found')).toHaveCount(0);
  await expect(page.getByText('Pump 7', { exact: false })).toHaveCount(0);
});

test('an assistant message with a persisted context_used record shows counts and no text', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page, {
    threadDetailMessages: [
      {
        id: 'server-context-message',
        role: 'assistant',
        content: 'Durable server history',
        timestamp: '2026-07-15T00:01:00Z',
        context_used: {
          recent_turns: { used: 4, available: 9 },
          summary: { through_sequence: 12 },
          preferences_used: 0,
          facts_used: 2,
          corpora: {
            controlled: { state: 'used', n: 3 },
            attachments: { state: 'not_consulted', n: 0 },
            // GR-16: filtered-out and empty read the same; no count.
            maintenance_records: { state: 'consulted_none', n: 0 }
          },
          topology: 'not_available',
          truncation: { recalled_episodes: 2 },
          retrieval_plan: { task_intent: 'diagnose', memory_types: ['fact'] },
          // The server's "not degraded" member is the STRING 'none'.
          degrade_reason: 'none',
          retrieval_envelopes: 3,
          // A text field must never reach the screen, even if it rides along.
          narrative: 'LEAKED-NARRATIVE-TEXT',
          items: [{ text: 'LEAKED-ITEM-TEXT' }]
        }
      }
    ]
  });

  await page.reload();
  await openChat(page);

  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  const disclosure = page.getByTestId('context-used-disclosure');
  await expect(disclosure).toBeVisible();
  await expect(disclosure.getByText('Context used')).toBeVisible();

  await disclosure.getByLabel('Toggle context used').click();
  await expect(disclosure.getByTestId('context-used-recent_turns')).toHaveText(
    '4 / 9'
  );
  await expect(disclosure.getByTestId('context-used-summary')).toHaveText(
    'through message 12'
  );
  await expect(disclosure.getByTestId('context-used-facts')).toHaveText('2');
  await expect(
    disclosure.getByTestId('context-used-corpus_controlled')
  ).toHaveText('used · 3');
  await expect(
    disclosure.getByTestId('context-used-corpus_attachments')
  ).toHaveText('not consulted');
  await expect(
    disclosure.getByTestId('context-used-corpus_maintenance_records')
  ).toHaveText('consulted - nothing usable');
  await expect(disclosure.getByTestId('context-used-degrade')).toHaveCount(0);
  await expect(disclosure.getByText('consulted_none')).toHaveCount(0);
  await expect(
    disclosure.getByTestId('context-used-truncation_recalled_episodes')
  ).toHaveText('recalled_episodes · 2');
  await expect(disclosure.getByTestId('context-used-task_intent')).toHaveText(
    'diagnose'
  );
  await expect(disclosure.getByTestId('context-used-envelopes')).toHaveText(
    '3'
  );

  await expect(page.getByText('LEAKED-NARRATIVE-TEXT')).toHaveCount(0);
  await expect(page.getByText('LEAKED-ITEM-TEXT')).toHaveCount(0);
  const html = await disclosure.innerHTML();
  expect(html).not.toContain('LEAKED');
});

test('a message without a context_used record shows no disclosure', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  await page.reload();
  await openChat(page);

  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  await expect(page.getByTestId('context-used-disclosure')).toHaveCount(0);
});

/**
 * The record every live wire carries below: counts, ids and closed codes,
 * plus two text fields that must never reach the screen or storage.
 */
const liveContextUsed = {
  recent_turns: { used: 2, available: 5 },
  summary: { through_sequence: 3 },
  preferences_used: 1,
  facts_used: 0,
  corpora: {
    controlled: { state: 'used', n: 2 },
    attachments: { state: 'consulted_none', n: 0 }
  },
  topology: 'not_available',
  truncation: {},
  retrieval_plan: {
    task_intent: 'diagnose',
    memory_types: ['fact', 'preference']
  },
  degrade_reason: 'none',
  retrieval_envelopes: 1,
  narrative: 'LEAKED-LIVE-TEXT',
  items: [{ text: 'LEAKED-LIVE-ITEM' }]
};

async function expectLiveDisclosure(page: Page, degraded: string | null) {
  // Exactly one: the streamed answer's. The reloaded history message has
  // no record and must not grow one.
  const disclosure = page.getByTestId('context-used-disclosure');
  await expect(disclosure).toHaveCount(1);
  await expect(disclosure).toBeVisible();
  await disclosure.getByLabel('Toggle context used').click();
  await expect(disclosure.getByTestId('context-used-recent_turns')).toHaveText(
    '2 / 5'
  );
  await expect(disclosure.getByTestId('context-used-summary')).toHaveText(
    'through message 3'
  );
  await expect(disclosure.getByTestId('context-used-preferences')).toHaveText(
    '1'
  );
  await expect(disclosure.getByTestId('context-used-facts')).toHaveText('0');
  await expect(
    disclosure.getByTestId('context-used-corpus_controlled')
  ).toHaveText('used · 2');
  await expect(
    disclosure.getByTestId('context-used-corpus_attachments')
  ).toHaveText('consulted - nothing usable');
  await expect(disclosure.getByTestId('context-used-task_intent')).toHaveText(
    'diagnose'
  );
  await expect(disclosure.getByTestId('context-used-memory_types')).toHaveText(
    'fact, preference'
  );
  if (degraded === null) {
    await expect(disclosure.getByTestId('context-used-degrade')).toHaveCount(0);
  } else {
    await expect(disclosure.getByTestId('context-used-degrade')).toHaveText(
      degraded
    );
  }

  await expect(page.getByText('LEAKED-LIVE-TEXT')).toHaveCount(0);
  await expect(page.getByText('LEAKED-LIVE-ITEM')).toHaveCount(0);
  expect(await disclosure.innerHTML()).not.toContain('LEAKED');
  // The allow-listed record is what reaches React state and storage.
  const stored = await page.evaluate(() =>
    localStorage.getItem('ai-chat-threads')
  );
  expect(stored ?? '').not.toContain('LEAKED');
}

test('the AG-UI aimms.contextUsed channel attaches a content-free disclosure to the streamed answer', async ({
  browser
}) => {
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
          delta: 'Live AG-UI answer'
        },
        { type: 'TEXT_MESSAGE_END', messageId: 'message-agui' },
        { type: 'CUSTOM', name: 'aimms.contextUsed', value: liveContextUsed },
        { type: 'RUN_FINISHED', threadId, runId: 'run-agui' }
      ])
    });
  });

  await page.reload();
  await page.evaluate(() => localStorage.setItem('aimms.wire', 'agui'));
  await openChat(page);
  await expect(page.getByTestId('context-used-disclosure')).toHaveCount(0);

  await page.getByPlaceholder('Type a message...').fill('What did you use?');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Live AG-UI answer', { exact: true })
  ).toBeVisible();

  await expectLiveDisclosure(page, null);
  await page.evaluate(() => localStorage.removeItem('aimms.wire'));
});

test('the legacy STATE_DELTA context_used frame attaches the same disclosure, including a real degrade code', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  await page.route('**/api/ai/chat/stream', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody([
        goldenEvents[0],
        goldenEvents[1],
        goldenEvents[2],
        { ...goldenEvents[3], delta: 'Live legacy answer' },
        goldenEvents[4],
        {
          type: 'STATE_DELTA',
          threadId,
          runId: 'contextUsed:1',
          kind: 'context_used',
          ...liveContextUsed,
          degrade_reason: 'budget_timeout'
        },
        goldenEvents[5]
      ])
    });
  });

  await page.evaluate(() => localStorage.removeItem('aimms.wire'));
  await page.reload();
  await openChat(page);
  await expect(page.getByTestId('context-used-disclosure')).toHaveCount(0);

  await page.getByPlaceholder('Type a message...').fill('What did you use?');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Live legacy answer', { exact: true })
  ).toBeVisible();

  await expectLiveDisclosure(page, 'budget_timeout');
});
