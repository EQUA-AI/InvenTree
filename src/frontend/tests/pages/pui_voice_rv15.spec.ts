/**
 * RV15 — mounted AIChatDrawer voice-turn callback acceptance
 * (AIChatDrawer.tsx onTurnResult via real useVoiceLiveSession →
 * VoiceSessionController listeners → isFreshSessionTurn gate).
 *
 * REAL: the drawer, hook, controller, stores, history UI and UserState
 * identity updates. MOCKED: transport/API/WebRTC/audio only, through the real
 * voice_harness installVoiceMocks/startVoice/emitTranscript/turnPayload
 * signatures. Native DOM/browser acceptance is mocked rendering — not
 * authenticated/provider/device acceptance.
 *
 * Identity contract (tranche 7 correction): the native New Chat action
 * creates a REAL generated thread (UseAIChat.createNewThread — a nonempty
 * thread id, NOT a null identity), and the voice create producer uses the
 * request thread when present (voice/routes.py:287) and returns the assigned
 * thread (routes.py:176). Modern positive fixtures therefore derive ONE
 * canonical thread id from the mocked create response and use it across
 * create/status/turn/history — measured in-test, never hand-typed. Deliberately
 * stale/out-of-thread results (rejection and legacy-adoption controls) pass an
 * explicit other thread id and are labelled as such below.
 */
import { type Page, type Route, expect, test } from '@playwright/test';

import {
  type VoiceObservations,
  emitTranscript,
  installVoiceMocks,
  openChat,
  sessionPayload,
  startVoice,
  turnPayload
} from './voice_harness.js';

const THREAD_X = 'thread-x';
const THREAD_SERVER = 'thread-server';

/** Visible titles exactly as the REAL history rows/thread selector render
 *  them (thread.title in AIChatDrawer.tsx ai-chat-history-row). */
const THREAD_TITLES: Record<string, string> = {
  'thread-x': 'Thread X',
  'thread-server': 'Thread Server',
  'thread-fresh': 'Thread Fresh'
};
const displayTitle = (threadId: string) =>
  THREAD_TITLES[threadId] ?? `Thread ${threadId}`;

/** Wire-valid POPULATED ConversationFocusRef targets ({kind, id, label}),
 *  DISTINCT per thread — selected-thread vs returned-thread focus differ so
 *  any focus poison is observable. Unknown threads (the generated New Chat
 *  thread) serve the populated labelled fallback below — never an invalid
 *  wire target. */
const FOCUS_BY_THREAD: Record<
  string,
  { kind: string; id: number; label: string }
> = {
  'thread-x': { kind: 'machine', id: 1, label: 'Machine One' },
  'thread-server': { kind: 'machine', id: 2, label: 'Machine Two' },
  'thread-fresh': { kind: 'machine', id: 3, label: 'Machine Three' }
};
const FOCUS_FALLBACK = { kind: 'machine', id: 1, label: 'Machine One' };

type DeferredTurn = {
  sessionId: string;
  accountUserId: number | null;
  release: (body: Record<string, unknown>) => Promise<void>;
};

type Rv15Harness = {
  voice: VoiceObservations | null;
  turns: DeferredTurn[];
  listReads: { accountUserId: number | null }[];
  detailReads: string[];
  focusReads: number;
  focusServed: {
    threadId: string;
    focus: { kind: string; id: number; label: string };
  }[];
  deletes: number;
  sessionIds: string[];
  /** Canonical thread per session id, as ASSIGNED by the mocked create
   *  producer (request thread honored — voice/routes.py:287/176 contract). */
  sessionThreads: Map<string, string>;
  /** Account identity under which each canonical thread's session was
   *  created (its list row is only served to that account). */
  threadAccounts: Map<string, number | null>;
  releasedTurns: {
    threadId: string;
    turnId: string;
    message: string;
    pendingQuestion: unknown;
  }[];
  /** Question objects echoed onto assistant HISTORY messages (the exact
   *  turn.pending_question object — UseAIChat.fetchServerThread maps
   *  ServerMessage.question to ChatMessage.question). */
  historyQuestions: { threadId: string; question: unknown }[];
};

/** Authentic wire bodies (shapes from tests/pages/aichat_harness.ts
 *  mockChatFoundation and the AimmsWire VoiceTurnResponse contract). */
const summaryPayload = (threadId: string) => ({
  mode: 'legacy_unconfirmed',
  version: 0,
  display_label: displayTitle(threadId)
});

const detailBody = (threadId: string): Record<string, any> => ({
  thread_id: threadId,
  title: displayTitle(threadId),
  summary: displayTitle(threadId),
  created_at: '2026-10-06T00:00:00Z',
  updated_at: '2026-10-06T00:01:00Z',
  active_scope: summaryPayload(threadId),
  shared: false,
  has_earlier: false,
  messages: [
    {
      id: `${threadId}-message`,
      role: 'assistant',
      content: `History for ${threadId}`,
      timestamp: '2026-10-06T00:01:00Z'
    }
  ]
});

const listRow = (threadId: string) => ({
  thread_id: threadId,
  title: displayTitle(threadId),
  message_count: 1,
  turn_count: 1,
  summary: displayTitle(threadId),
  created_at: '2026-10-06T00:00:00Z',
  last_activity: '2026-10-06T00:01:00Z',
  is_persisted: true,
  active_scope: summaryPayload(threadId)
});

const listBody = (accountUserId: number | null, extraThreads: string[]) => ({
  // New-account reads receive NEW data — never the old account's rows. Each
  // account also sees the canonical thread(s) ITS OWN voice sessions were
  // created for (production-shaped: the server thread list contains the
  // session's thread), never another account's threads.
  threads: (accountUserId === 22
    ? [listRow('thread-fresh')]
    : [listRow(THREAD_X), listRow(THREAD_SERVER)]
  ).concat(extraThreads.map(listRow)),
  sync_token: null,
  has_more: false,
  capabilities: { thread_scope: true, thread_focus: true }
});

/** VoiceTurnResponse with a NONEMPTY pending question (VoicePendingQuestion
 *  wire shape) so a dropped result is a proven rejection, not an empty no-op.
 *  `threadId` is EXPLICIT: modern positive fixtures pass the canonical
 *  session thread (from the mocked create assignment); rejection/legacy
 *  controls pass a deliberately different thread and say so at the call
 *  site. Nothing here hardcodes a thread that disagrees with the session. */
const lateTurnBody = (
  sessionId: string,
  threadId: string,
  questionText: string,
  turnSuffix = ''
) =>
  turnPayload({
    session_id: sessionId,
    thread_id: threadId,
    turn_id: `turn-${sessionId}${turnSuffix}`,
    message: 'RV15 mocked answer',
    response_state: 'complete',
    // A turn's echoed focus snapshot (never moves the visible chip).
    focus: null,
    focus_version: 0,
    // Wire-COMPLETE VoicePendingQuestion (AimmsWire.generated.ts:245-260):
    // kind plus every option's kind/description/recommended fields. kind is
    // the armQuestion consumer's QuestionPayload literal (UseAIChat.tsx:463).
    pending_question: {
      kind: 'clarification_question',
      interrupt_id: `q-${sessionId}${turnSuffix}`,
      question_text: questionText,
      options: [
        {
          id: 'opt-yes',
          label: 'Yes',
          kind: null,
          description: null,
          recommended: null
        },
        {
          id: 'opt-no',
          label: 'No',
          kind: null,
          description: null,
          recommended: null
        }
      ],
      expires_at: null,
      source: 'voice'
    }
  });

async function setupRv15(page: Page): Promise<Rv15Harness> {
  const harness: Rv15Harness = {
    voice: null,
    turns: [],
    listReads: [],
    detailReads: [],
    focusReads: 0,
    focusServed: [],
    deletes: 0,
    sessionIds: [],
    sessionThreads: new Map<string, string>(),
    threadAccounts: new Map<string, number | null>(),
    releasedTurns: [],
    historyQuestions: []
  };

  // Non-loopback guard: registered FIRST (lowest precedence fallback) — any
  // request that is not served by the local fixture server is aborted.
  await page.route('**/*', async (route: Route) => {
    const url = route.request().url();
    if (url.startsWith('http://127.0.0.1:5179/')) return route.continue();
    return route.abort();
  });

  await page.route('**/api/approvals/count/**', (route: Route) =>
    route.fulfill({ json: { count: 0 } })
  );

  await page.route('**/api/ai/threads**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const accountUserId = await page.evaluate(() =>
      (window as any).__rv15.accountUserId()
    );
    // Focus endpoints share the /threads glob — branch before the detail
    // fallthrough (same ordering contract as mockChatFoundation).
    if (/\/threads\/[^/]+\/focus$/.test(url.pathname)) {
      const focusThreadId = decodeURIComponent(
        url.pathname.match(/\/threads\/([^/]+)\/focus$/)![1]
      );
      // POPULATED DISTINCT per-thread focus (ConversationFocusRef wire,
      // {kind, id, label} — label required): reads always carry
      // focus_unavailable (conversationFocus.ts contract).
      const focusRef = FOCUS_BY_THREAD[focusThreadId] ?? FOCUS_FALLBACK;
      if (request.method() === 'PUT') {
        await route.fulfill({
          json: { focus: focusRef, focus_version: 4, focus_unavailable: false }
        });
        return;
      }
      harness.focusReads += 1;
      harness.focusServed.push({ threadId: focusThreadId, focus: focusRef });
      await route.fulfill({
        json: { focus: focusRef, focus_version: 3, focus_unavailable: false }
      });
      return;
    }
    if (/\/threads\/[^/]+\/scope$/.test(url.pathname)) {
      const threadId = decodeURIComponent(
        url.pathname.match(/\/threads\/([^/]+)\/scope$/)![1]
      );
      await route.fulfill({
        json: {
          thread_id: threadId,
          scope: {
            schema_version: 1,
            mode: 'legacy_unconfirmed',
            machine_ids: [],
            date_window: { from: null, to: null },
            source_classes: [],
            display_label: ''
          },
          version: 0,
          hash: '',
          display_label: '',
          editable: true
        }
      });
      return;
    }
    const detailMatch = url.pathname.match(/\/threads\/([^/]+)$/);
    if (detailMatch && request.method() === 'GET') {
      const threadId = decodeURIComponent(detailMatch[1]);
      harness.detailReads.push(threadId);
      const body = detailBody(threadId);
      // Typed and voice turns share one server history: released turns bound
      // to this thread are echoed into the resynced conversation (the drawer
      // renders the converged conversation from this read). The echoed
      // question is the EXACT turn.pending_question object (real persisted
      // thread history stores the turn's question on its message).
      for (const turn of harness.releasedTurns) {
        if (turn.threadId === threadId) {
          body.messages.push({
            id: turn.turnId,
            role: 'assistant',
            content: turn.message,
            timestamp: '2026-10-06T00:02:00Z',
            question: turn.pendingQuestion ?? undefined
          });
          if (turn.pendingQuestion) {
            harness.historyQuestions.push({
              threadId,
              question: turn.pendingQuestion
            });
          }
        }
      }
      await route.fulfill({ json: body });
      return;
    }
    const extraThreads = [
      ...new Set([...harness.sessionThreads.values()])
    ].filter(
      (t) =>
        ![THREAD_X, THREAD_SERVER, 'thread-fresh'].includes(t) &&
        harness.threadAccounts.get(t) === accountUserId
    );
    harness.listReads.push({ accountUserId });
    await route.fulfill({ json: listBody(accountUserId, extraThreads) });
  });

  // Real voice transport mocks (WebRTC/getUserMedia/AudioContext/API).
  harness.voice = await installVoiceMocks(page);

  // Session identity: each real session create gets its OWN id (so session
  // replacement is observable) and a PRODUCTION-SHAPED assigned thread — the
  // real producer (voice/routes.py:287) uses the request's thread selection
  // when present, otherwise assigns a server thread, and its serializer
  // (routes.py:176) emits that assigned thread. The mocked create honors the
  // request thread, so the canonical session thread is the REAL generated
  // New Chat thread created by UseAIChat.createNewThread (a nonempty bound
  // identity — never null); status/turn/history below all resolve through
  // this same canonical assignment.
  await page.route('**/api/ai/voice/sessions', async (route: Route) => {
    if (route.request().method() !== 'POST') {
      await route.fulfill({ json: {} });
      return;
    }
    const id = `rv15-session-${harness.sessionIds.length + 1}`;
    harness.sessionIds.push(id);
    const createBody = route.request().postDataJSON() as {
      thread_id?: string | null;
    } | null;
    const assignedThread = createBody?.thread_id ?? THREAD_SERVER;
    harness.sessionThreads.set(id, assignedThread);
    // The generated New Chat thread presents as 'New Chat' everywhere
    // (selector, history rows, thread detail) — a presentation label for the
    // REAL nonempty thread id, never a null identity.
    if (!(assignedThread in THREAD_TITLES))
      THREAD_TITLES[assignedThread] = 'New Chat';
    harness.threadAccounts.set(
      assignedThread,
      await page.evaluate(() => (window as any).__rv15.accountUserId())
    );
    await route.fulfill({
      status: 201,
      json: sessionPayload({
        id,
        thread_id: assignedThread,
        focus: null,
        focus_version: 0
      })
    });
  });
  await page.route('**/api/ai/voice/sessions/*', async (route: Route) => {
    const request = route.request();
    const id =
      new URL(request.url()).pathname.split('/').filter(Boolean).pop() ?? '';
    if (request.method() === 'DELETE') {
      harness.deletes += 1;
      await route.fulfill({ json: { id, state: 'ended' } });
      return;
    }
    await route.fulfill({
      json: sessionPayload({
        id,
        thread_id: harness.sessionThreads.get(id) ?? THREAD_SERVER,
        focus: null,
        focus_version: 0
      })
    });
  });

  // Deferred turn responses: captured (tagged with the account under which the
  // request arrived) and released later by the test — the exact "late result"
  // seam into the real controller → drawer onTurnResult callback.
  await page.route('**/api/ai/voice/sessions/*/turns', async (route: Route) => {
    const request = route.request();
    const sessionId =
      new URL(request.url()).pathname.split('/').filter(Boolean).slice(-2)[0] ??
      '';
    const accountUserId = await page.evaluate(() =>
      (window as any).__rv15.accountUserId()
    );
    await new Promise<void>((resolve) => {
      harness.turns.push({
        sessionId,
        accountUserId,
        release: async (body: Record<string, unknown>) => {
          harness.releasedTurns.push({
            threadId: String(body.thread_id ?? ''),
            turnId: String(body.turn_id ?? ''),
            message: String(body.message ?? ''),
            // The EXACT released turn.pending_question is mirrored onto the
            // assistant message below (real persisted thread history stores
            // the turn's question on its message; UseAIChat fetchServerThread
            // maps ServerMessage.question -> ChatMessage.question).
            pendingQuestion: body.pending_question ?? null
          });
          await route.fulfill({ status: 200, json: body });
          resolve();
        }
      });
    });
  });

  await page.goto('/playwright/voice-rv15.html');
  // Real mounted-ready marker (fixture sets it after first committed render).
  const ready = await page
    .waitForFunction(
      () => (document.body as any).dataset?.rv15Ready === '1',
      undefined,
      { timeout: 12000 }
    )
    .then(() => true)
    .catch(() => false);
  if (!ready) {
    const errors = await page.evaluate(
      () => (window as any).__rv15Errors ?? []
    );
    throw new Error(
      `RV15 fixture never mounted; page errors: ${JSON.stringify(errors)}`
    );
  }
  await openChat(page);
  return harness;
}

/** Native New Chat action through the real thread-selector UI. NOTE: this is
 *  NOT "no selected thread" — UseAIChat.createNewThread (via
 *  AIChatDrawer.handleNewThread) generates a REAL nonempty thread id and the
 *  drawer's voice binding/session creation use it; the visible label is the
 *  presentation text 'New Chat', not a null identity. */
async function ensureNewChat(page: Page) {
  await page.getByLabel('select-ai-chat-thread').click();
  // Native visible selector: the thread selector renders BOTH an ActionIcon
  // button and the dropdown's menu item under this aria-label — the
  // deliberate action after opening the selector is the menu item.
  await page.getByRole('menuitem', { name: 'new-ai-chat-thread' }).click();
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'New Chat'
  );
}

/** Select a thread through the REAL history UI (ai-chat-history-row on the
 *  drawer's History tab — the thread selector button is disabled while a
 *  voice session is live, so the deliberate switch uses the history rows
 *  themselves). */
async function selectThreadViaHistoryUi(page: Page, title: string) {
  const row = page
    .getByTestId('ai-chat-history-row')
    .filter({ hasText: title });
  const visible = await row
    .first()
    .waitFor({ state: 'visible', timeout: 1500 })
    .then(() => true)
    .catch(() => false);
  if (!visible) {
    await page.getByRole('tab', { name: 'History' }).click();
  }
  await row.first().click();
}

/**
 * Bounded absence witness for a REJECTED late result. Readiness comes from an
 * actual response-processing witness BEFORE calling this (the drawer
 * callback's syncThreads() list resync, or the transport-level release of a
 * result the client may drop pre-callback); this then samples the REAL DOM
 * and controller counters across a bounded window and requires EVERY sample
 * to show the rejected effects absent — never a naked fixed sleep.
 */
async function expectRejectedEffectsStayAbsent(
  page: Page,
  h: Rv15Harness,
  expected: { selectorText: string; questionText: string; focusReads: number }
): Promise<void> {
  const samples: {
    ok: boolean;
    selector: string;
    questions: number;
    focusReads: number;
  }[] = [];
  const deadline = Date.now() + 1800;
  while (Date.now() < deadline) {
    const selector = await page.getByLabel('select-ai-chat-thread').innerText();
    const questions = await page.getByText(expected.questionText).count();
    samples.push({
      ok:
        selector.includes(expected.selectorText) &&
        questions === 0 &&
        h.focusReads === expected.focusReads,
      selector,
      questions,
      focusReads: h.focusReads
    });
    await page.waitForTimeout(150);
  }
  expect(samples.length).toBeGreaterThanOrEqual(8);
  expect(
    samples.filter((s) => !s.ok),
    `rejected effects observed: ${JSON.stringify(samples)}`
  ).toEqual([]);
}

test('late out-of-thread result is dropped after a deliberate thread switch (drawer gate)', async ({
  page
}) => {
  const h = await setupRv15(page);
  await ensureNewChat(page);
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'New Chat'
  );

  // The turn is captured while the REAL generated New Chat thread is the
  // visible/bound selection (a nonempty thread id — not "no selection"). The
  // late result released below is deliberately OUT-OF-THREAD (thread-server)
  // — the stale-result shape this rejection control is about.
  await emitTranscript(page, { text: 'late words', itemId: 'item-late' });
  await expect.poll(() => h.turns.length).toBe(1);
  expect(h.turns[0].sessionId).toBe('rv15-session-1');
  expect(h.turns[0].accountUserId).toBe(11);

  // Deliberate selection through the real history UI.
  await selectThreadViaHistoryUi(page, 'Thread X');
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'Thread X'
  );
  const focusBefore = h.focusReads;
  const resyncReadsBefore = h.listReads.length + h.detailReads.length;

  // Release the captured old-session result for thread-server (nonempty
  // pending question — rejection must be proven against real content).
  await h.turns[0].release(
    lateTurnBody('rv15-session-1', THREAD_SERVER, 'RV15 late question?')
  );

  // Bounded witnessed absence FIRST: the control's contract is that the late
  // result leaves NO stale effects (thread/focus/question) — sampled across a
  // bounded window of real DOM/controller state after the transport release.
  await expectRejectedEffectsStayAbsent(page, h, {
    selectorText: 'Thread X',
    questionText: 'RV15 late question?',
    focusReads: focusBefore
  });
  // Honest attribution A (end-to-end mocked transport): the real
  // VoiceSessionController dispatched this result to NO registered listener —
  // it was dropped BEFORE any drawer callback (configure()->end() on the real
  // thread switch invalidates the generation and aborts the request). This
  // leg alone is NOT drawer-gate evidence and is not claimed as such.
  expect(
    await page.evaluate(() => (window as any).__rv15.controllerDispatches())
  ).toEqual([]);
  expect(h.listReads.length + h.detailReads.length).toBe(resyncReadsBefore);
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'Thread X'
  );
  await expect(page.getByText('RV15 late question?')).toHaveCount(0);
  expect(h.focusReads).toBe(focusBefore);

  // Honest attribution B (drawer-callback isolation, TEST-ONLY seam,
  // parent-authorized): deliver the SAME stale result DIRECTLY to the ACTUAL
  // mounted listener registered by the real useVoiceLiveSession — i.e. into
  // the real AIChatDrawer onTurnResult React callback (no recreated helper,
  // no mocked hook). Callback-invocation witness: that callback runs
  // syncThreads() BEFORE its gate, so new thread reads prove it ran.
  const callbackReadsBefore = h.listReads.length + h.detailReads.length;
  const delivered = await page.evaluate(
    (turn) => (window as any).__rv15.deliverTurnToMountedListeners(turn),
    lateTurnBody(
      'rv15-session-1',
      THREAD_SERVER,
      'RV15 gate-isolation question?'
    ) as unknown as Record<string, unknown>
  );
  expect(delivered).toBeGreaterThanOrEqual(1);
  await expect
    .poll(() => h.listReads.length + h.detailReads.length)
    .toBeGreaterThan(callbackReadsBefore);
  // The REAL mounted drawer callback RAN and its isFreshSessionTurn gate still
  // rejected the stale result: no visible thread move, no question arming.
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'Thread X'
  );
  await expect(page.getByText('RV15 gate-isolation question?')).toHaveCount(0);
  expect(h.focusReads).toBe(focusBefore);
  // Populated focus, no poison: the SELECTED thread's populated focus
  // (machine 1, labelled) survives; the returned thread's DISTINCT populated
  // focus (machine 2, labelled) never lands. Labels are required wire.
  expect(
    await page.evaluate(() => (window as any).__rv15.threadFocus())
  ).toContain('"id":1');
  expect(
    await page.evaluate(() => (window as any).__rv15.threadFocus())
  ).toContain('Machine One');
  expect(
    await page.evaluate(() => (window as any).__rv15.threadFocus())
  ).not.toContain('"id":2');
});

test('account/session replacement drops the old result; fresh-account result works', async ({
  page
}) => {
  const h = await setupRv15(page);
  await ensureNewChat(page);
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await emitTranscript(page, {
    text: 'old account words',
    itemId: 'item-old'
  });
  await expect.poll(() => h.turns.length).toBe(1);
  expect(h.turns[0].accountUserId).toBe(11);

  // Real teardown: closing the drawer ends the voice session.
  await page.getByTestId('rv15-close-drawer').click();
  await expect.poll(() => h.deletes).toBe(1);
  await page.getByTestId('rv15-open-drawer').click();

  // Real UserState identity flip (pk AND userId() together, with rerender).
  await page.evaluate(() => (window as any).__rv15.flipAccount());
  await expect
    .poll(() => page.evaluate(() => (window as any).__rv15.accountUserId()))
    .toBe(22);

  // Fresh-account session: bound to the account's REAL generated New Chat
  // thread (nonempty identity), and new-account reads get new data.
  await ensureNewChat(page);
  await startVoice(page);
  await expect.poll(() => h.sessionIds.length).toBe(2);
  // Actual readiness witness (same contract as tests 1/3): the controller
  // reached Listening before a transcript can generate a turn.
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await emitTranscript(page, {
    text: 'fresh account words',
    itemId: 'item-fresh'
  });
  await expect.poll(() => h.turns.length).toBe(2);
  expect(h.turns[1].accountUserId).toBe(22);

  const focusBefore = h.focusReads;
  const oldAccountReadsBefore = h.listReads.filter(
    (read) => read.accountUserId === 11
  ).length;
  // The OLD session's result is released late (deliberately out-of-thread,
  // thread-server): it must not adopt/arm/replace.
  await h.turns[0].release(
    lateTurnBody('rv15-session-1', THREAD_SERVER, 'RV15 old-account question?')
  );
  // Bounded witnessed absence: the transport delivery above is the processing
  // witness for a result the client may drop before the drawer callback; the
  // real DOM/controller state must stay untouched across the bounded window.
  await expectRejectedEffectsStayAbsent(page, h, {
    selectorText: 'New Chat',
    questionText: 'RV15 old-account question?',
    focusReads: focusBefore
  });
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'New Chat'
  );
  await expect(page.getByText('RV15 old-account question?')).toHaveCount(0);
  expect(h.focusReads).toBe(focusBefore);
  // Honest attribution: the old-account result reached NO registered
  // listener — dropped pre-callback (controller fence), not drawer-gated.
  expect(
    await page.evaluate(() => (window as any).__rv15.controllerDispatches())
  ).toEqual([]);
  // No poisoning: the dropped old-account result must never re-read the
  // threads list under the OLD identity after the flip.
  expect(h.listReads.filter((read) => read.accountUserId === 11).length).toBe(
    oldAccountReadsBefore
  );
  // Selected populated focus (machine 1, labelled) survives the denial; the
  // denied result's DISTINCT returned-thread focus (machine 2) never lands.
  const focusStateAfterDenial = await page.evaluate(() =>
    (window as any).__rv15.threadFocus()
  );
  expect(focusStateAfterDenial).toContain('"id":1');
  expect(focusStateAfterDenial).toContain('Machine One');
  expect(focusStateAfterDenial).not.toContain('"id":2');
  expect(h.focusServed.map((f) => f.threadId)).not.toContain(THREAD_SERVER);

  // The fresh-account result still converges (the guard is not a disable).
  // LEGACY server-thread adoption recovery (initial-adoption semantics,
  // deliberately non-canonical returned thread — kept as the retained legacy
  // adoption control, NOT claimed as a modern canonical positive).
  const freshReadsBefore = h.listReads.filter(
    (read) => read.accountUserId === 22
  ).length;
  await h.turns[1].release(
    lateTurnBody('rv15-session-2', THREAD_SERVER, 'RV15 fresh question?')
  );
  // Fresh-account reads recover under the NEW identity with NEW data.
  await expect
    .poll(() => h.listReads.filter((read) => read.accountUserId === 22).length)
    .toBeGreaterThan(freshReadsBefore);
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'Thread Server'
  );
  await expect(page.getByText('RV15 fresh question?')).toBeVisible();
  await expect.poll(() => h.focusReads).toBeGreaterThan(focusBefore);
  // The FRESH result WAS dispatched by the real controller end-to-end
  // (acceptance measured on the same witness as rejection).
  expect(
    await page.evaluate(() => (window as any).__rv15.controllerDispatches())
  ).toEqual(['rv15-session-2']);
  // Populated RETURNED-thread focus (machine 2, labelled) lands on the
  // mounted state — DISTINCT from the selected machine-1 focus above.
  await expect
    .poll(() => page.evaluate(() => (window as any).__rv15.threadFocus()))
    .toContain('"id":2');
  expect(
    await page.evaluate(() => (window as any).__rv15.threadFocus())
  ).toContain('Machine Two');
});

test('bound New Chat creation and same-thread fresh results converge on the canonical thread', async ({
  page
}) => {
  const h = await setupRv15(page);
  // Modern production-bound creation: the native New Chat action produces the
  // REAL generated thread (nonempty bound identity), and the mocked create
  // producer honors the request thread (voice/routes.py:287) and returns it
  // (routes.py:176). The canonical thread is DERIVED from the create
  // response — status/turn/history must agree with it (measured, not typed).
  await ensureNewChat(page);
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await emitTranscript(page, { text: 'adopt words', itemId: 'item-adopt' });
  await expect.poll(() => h.turns.length).toBe(1);
  const canonical = h.sessionThreads.get('rv15-session-1') ?? '';
  expect(
    canonical,
    'mock create must assign the requested canonical thread'
  ).toBeTruthy();
  expect(h.turns[0].sessionId).toBe('rv15-session-1');

  const focusBefore = h.focusReads;
  const prepared = lateTurnBody(
    'rv15-session-1',
    canonical,
    'RV15 adoption question?'
  );
  // Modern producer/consumer chain: the turn response carries the SAME
  // canonical thread as create/status; its pending question is echoed onto
  // the assistant HISTORY message for that thread (same object).
  await h.turns[0].release(prepared);
  // Same-thread convergence on the BOUND New Chat thread: no thread move (the
  // controller never treats a same-thread arrival as a switch) and NO native
  // teardown — the visible New Chat selection is kept.
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'New Chat'
  );
  // Converged conversation read through the REAL history UI (re-selecting
  // the SAME thread is not a thread switch — configure() never treats the
  // session's own thread as a switch): the drawer renders the converged
  // conversation from this read (UseAIChat.fetchServerThread maps the echoed
  // ServerMessage.question onto ChatMessage.question), so the accepted
  // question renders from the SAME stored question object.
  await selectThreadViaHistoryUi(page, 'New Chat');
  await expect(page.getByText('RV15 adoption question?')).toBeVisible();
  await expect.poll(() => h.focusReads).toBeGreaterThan(focusBefore);
  // Accepted question provenance: the EXACT turn.pending_question object is
  // echoed on the assistant HISTORY message (UseAIChat.fetchServerThread maps
  // it to ChatMessage.question) — the rendered question needs both.
  await expect.poll(() => h.historyQuestions.length).toBeGreaterThan(0);
  expect(h.historyQuestions).toContainEqual({
    threadId: canonical,
    question: prepared.pending_question
  });
  expect(h.releasedTurns[0]).toMatchObject({
    threadId: canonical,
    turnId: String(prepared.turn_id)
  });
  // Real controller dispatched the converged result end-to-end...
  expect(
    await page.evaluate(() => (window as any).__rv15.controllerDispatches())
  ).toEqual(['rv15-session-1']);
  // ...and the converged thread's DISTINCT POPULATED, LABELLED focus target
  // ({kind, id, label} — label required) landed on the mounted state.
  await expect
    .poll(() => h.focusServed.some((f) => f.threadId === canonical))
    .toBe(true);
  expect(h.focusServed.find((f) => f.threadId === canonical)?.focus).toEqual({
    kind: 'machine',
    id: 1,
    label: 'Machine One'
  });
  await expect
    .poll(() => page.evaluate(() => (window as any).__rv15.threadFocus()))
    .toContain('"id":1');
  expect(
    await page.evaluate(() => (window as any).__rv15.threadFocus())
  ).toContain('Machine One');
  expect(
    await page.evaluate(() => (window as any).__rv15.threadFocus())
  ).not.toContain('"id":2');

  // Same-thread fresh result converges as well — and the second turn IS
  // captured (the session survived the first convergence: no mismatched
  // thread switch, no configure()->end() teardown).
  await emitTranscript(page, {
    text: 'same thread words',
    itemId: 'item-same'
  });
  await expect.poll(() => h.turns.length).toBe(2);
  await h.turns[1].release(
    lateTurnBody(
      'rv15-session-1',
      canonical,
      'RV15 same-thread question?',
      '-b'
    )
  );
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'New Chat'
  );
  // Same converged-conversation read as leg 1 (same thread, no switch).
  await selectThreadViaHistoryUi(page, 'New Chat');
  await expect(page.getByText('RV15 same-thread question?')).toBeVisible();
  // Both same-thread results came through the real controller dispatch.
  expect(
    await page.evaluate(() => (window as any).__rv15.controllerDispatches())
  ).toEqual(['rv15-session-1', 'rv15-session-1']);
  await expect
    .poll(() => page.evaluate(() => (window as any).__rv15.threadFocus()))
    .toContain('"id":1');
  // The genuine session is still live (no native teardown anywhere above).
  expect(h.deletes).toBe(0);
  expect(
    await page.evaluate(() => (window as any).__rv15.activeVoiceSessionId())
  ).toBe('rv15-session-1');
});

test('callback-at-configure-boundary: bound-thread mismatch result stays fenced while the session is current', async ({
  page
}) => {
  const h = await setupRv15(page);
  // Creation-time visible selection is the REAL generated New Chat thread
  // (bound nonempty identity), even though the deliberate result delivered
  // below returns a different (server) thread.
  await ensureNewChat(page);
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'New Chat'
  );
  await emitTranscript(page, { text: 'gap words', itemId: 'item-gap' });
  await expect.poll(() => h.turns.length).toBe(1);
  expect(h.turns[0].sessionId).toBe('rv15-session-1');
  // The captured turn's HTTP reply stays HELD for the whole gap case. The
  // prepared result is deliberately OUT-OF-THREAD (thread-server).
  const prepared = lateTurnBody(
    'rv15-session-1',
    THREAD_SERVER,
    'RV15 gap question?',
    '-gap'
  );

  // Stage the EXACT prepared turn.pending_question onto the mocked persisted
  // assistant history (history can exist while the turn HTTP reply is held):
  // the real drawer's rendered question needs BOTH an armed interrupt AND the
  // stored ChatMessage.question, so a wrongly adopting gate would render this
  // exact question here. Rejection below is proven against this SAME question
  // text — never a differently echoed question.
  h.releasedTurns.push({
    threadId: THREAD_SERVER,
    turnId: String(prepared.turn_id),
    message: String(prepared.message),
    pendingQuestion: prepared.pending_question ?? null
  });

  // TEST-ONLY one-shot configure-boundary seam (parent-authorized): the NEXT
  // real voiceController.configure call for thread-x delivers this prepared
  // stale server-thread result to the ACTUAL mounted listeners BEFORE the
  // original configure method runs — i.e. before the genuine native switch
  // teardown, while the session is still current and nonempty. Isolated
  // callback-at-configure-boundary delivery: NOT provider/transport/
  // native-controller dispatch (the observer listener is excluded, so the
  // controller dispatch log stays empty for it). This case exercises the
  // UNCHANGED bound-thread mismatch rejection (drawer binding exists and no
  // longer matches the visible thread) — NOT the changed createdThreadId==
  // null predicate (that is the next case).
  await page.evaluate(
    (turn) => (window as any).__rv15.armConfigureBoundary('thread-x', turn),
    prepared as unknown as Record<string, unknown>
  );
  const readsBefore = h.listReads.length + h.detailReads.length;

  // Real deliberate selection through the history UI — the actual native
  // configure call the seam intercepts (real selected thread-X).
  await selectThreadViaHistoryUi(page, 'Thread X');
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'Thread X'
  );

  // Witness: the active REAL session id before delivery was nonempty and
  // matched the delivered turn's session, the real configure input was
  // thread-X, and the ACTUAL mounted callback was invoked.
  const witness = await page.evaluate(() =>
    (window as any).__rv15.configureBoundaryWitness()
  );
  expect(witness).toHaveLength(1);
  expect(witness[0].configureThreadId).toBe('thread-x');
  expect(witness[0].sessionBeforeDelivery).toBe('rv15-session-1');
  expect(witness[0].sessionBeforeDelivery).toBe(prepared.session_id);
  expect(witness[0].delivered).toBeGreaterThanOrEqual(1);
  // Seam delivery is deliberately excluded from the controller dispatch log.
  expect(
    await page.evaluate(() => (window as any).__rv15.controllerDispatches())
  ).toEqual([]);
  // Actual mounted callback invocation: the real drawer callback's
  // syncThreads() reads ran before its gate. (This increment also contains
  // the legitimate thread-X selection fetches — disclosed confound; the
  // delivered count above is the direct real-listener invocation witness.)
  await expect
    .poll(() => h.listReads.length + h.detailReads.length)
    .toBeGreaterThan(readsBefore);

  // Selected focus (thread-X, machine id 1) landed before sampling.
  await expect
    .poll(() => page.evaluate(() => (window as any).__rv15.threadFocus()))
    .toContain('"id":1');
  const focusSettled = h.focusReads;
  // Bounded witnessed absence across the REAL DOM: the bound-thread mismatch
  // fence keeps thread-X selected, the exact staged question unarmed, and the
  // returned thread's focus unapplied — even though the delivered turn's
  // session matched and was still current at delivery.
  await expectRejectedEffectsStayAbsent(page, h, {
    selectorText: 'Thread X',
    questionText: 'RV15 gap question?',
    focusReads: focusSettled
  });
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'Thread X'
  );
  await expect(page.getByText('RV15 gap question?')).toHaveCount(0);
  // Selected focus id1 unchanged; returned focus id2 never applied.
  expect(h.focusServed.map((f) => f.threadId)).not.toContain(THREAD_SERVER);
  expect(
    await page.evaluate(() => (window as any).__rv15.threadFocus())
  ).toContain('"id":1');
  expect(
    await page.evaluate(() => (window as any).__rv15.threadFocus())
  ).not.toContain('"id":2');

  // The seam forwarded to the ORIGINAL configure method (finally block): the
  // genuine native switch teardown then ran and cleared the live session.
  await expect.poll(() => h.deletes).toBe(1);
  await expect
    .poll(() =>
      page.evaluate(() => (window as any).__rv15.activeVoiceSessionId())
    )
    .toBe(null);
});

test('first-snapshot missing-binding result with a differing visible thread stays rejected (changed-predicate regression)', async ({
  page
}) => {
  const h = await setupRv15(page);
  // Deliberate visible selection BEFORE any voice session exists, so the
  // drawer's voice binding has genuinely never been captured: real history-UI
  // switch to thread-X (populated labelled focus machine 1).
  await selectThreadViaHistoryUi(page, 'Thread X');
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'Thread X'
  );
  await expect
    .poll(() => page.evaluate(() => (window as any).__rv15.threadFocus()))
    .toContain('"id":1');
  const focusBefore = h.focusReads;

  // The delivered result is deliberately OUT-OF-THREAD (thread-server) with a
  // wire-complete pending question, so the visible selection differs from the
  // delivered turn's thread.
  const prepared = lateTurnBody(
    'rv15-session-1',
    THREAD_SERVER,
    'RV15 missing-binding question?',
    '-mb'
  );
  // Stage the EXACT prepared question onto the mocked persisted assistant
  // history for the result's thread: a wrongly admitting gate would arm AND
  // render this exact question (ChatMessage.question mapping), so rejection
  // is proven against the SAME question text/object — never a different one.
  h.releasedTurns.push({
    threadId: THREAD_SERVER,
    turnId: String(prepared.turn_id),
    message: String(prepared.message),
    pendingQuestion: prepared.pending_question ?? null
  });

  // TEST-ONLY one-shot first-session-snapshot seam (parent-authorized): the
  // prepared result is delivered to the ACTUAL mounted listeners inside the
  // GENUINE native update({ session: created }) transition (forwarded
  // unchanged) — synchronously AFTER the real snapshot lands (real session
  // id nonempty and matching) and BEFORE the task can unwind to React, i.e.
  // before the drawer's voice binding is FIRST captured on render
  // (AIChatDrawer.tsx:1435-1443). createdThreadId is therefore genuinely
  // MISSING (never forged, no ref reset, no helper/hook replacement, and the
  // native stale-start fence and teardown are untouched).
  await page.evaluate(
    (turn) => (window as any).__rv15.armFirstSessionSnapshot(turn),
    prepared as unknown as Record<string, unknown>
  );
  const readsBefore = h.listReads.length + h.detailReads.length;
  await startVoice(page);
  await expect(page.getByTestId('voice-state-badge')).toHaveText('Listening');

  // Seam witness: genuine first snapshot transition (null -> nonempty), the
  // real session id nonempty and matching the delivered turn's session, the
  // ACTUAL mounted listener(s) invoked (the marked observer is excluded —
  // this is never counted as a native controller dispatch), and delivery
  // before any native teardown.
  const witness = await page.evaluate(() =>
    (window as any).__rv15.firstSessionSnapshotWitness()
  );
  expect(witness).toHaveLength(1);
  expect(witness[0].seam).toBe('first-session-snapshot');
  expect(witness[0].sessionBeforeUpdate).toBe(null);
  expect(witness[0].sessionAfterUpdate).toBe('rv15-session-1');
  expect(witness[0].sessionAfterUpdate).toBe(prepared.session_id);
  expect(witness[0].turnSessionId).toBe(prepared.session_id);
  expect(witness[0].delivered).toBeGreaterThanOrEqual(1);
  expect(h.deletes).toBe(0);
  expect(
    await page.evaluate(() => (window as any).__rv15.controllerDispatches())
  ).toEqual([]);
  // Actual mounted callback invocation witness: the real callback runs
  // syncThreads() BEFORE its gate.
  await expect
    .poll(() => h.listReads.length + h.detailReads.length)
    .toBeGreaterThan(readsBefore);

  // The CHANGED predicate (createdThreadId == null admits only no visible
  // thread or the result's own thread) rejects this result: the selected
  // thread is preserved, the exact question stays unarmed, the selected
  // populated labelled focus is preserved and the returned thread's DISTINCT
  // focus never lands.
  const focusSettled = h.focusReads;
  await expectRejectedEffectsStayAbsent(page, h, {
    selectorText: 'Thread X',
    questionText: 'RV15 missing-binding question?',
    focusReads: focusSettled
  });
  await expect(page.getByLabel('select-ai-chat-thread')).toContainText(
    'Thread X'
  );
  await expect(page.getByText('RV15 missing-binding question?')).toHaveCount(0);
  expect(h.focusServed.map((f) => f.threadId)).not.toContain(THREAD_SERVER);
  const focusState = await page.evaluate(() =>
    (window as any).__rv15.threadFocus()
  );
  expect(focusState).toContain('"id":1');
  expect(focusState).toContain('Machine One');
  expect(focusState).not.toContain('"id":2');
  // Delivery happened before native teardown; the session is still live.
  expect(h.deletes).toBe(0);
  expect(
    await page.evaluate(() => (window as any).__rv15.activeVoiceSessionId())
  ).toBe('rv15-session-1');
});
