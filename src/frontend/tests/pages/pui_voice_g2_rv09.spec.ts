/**
 * G2 RV09 mounted baseline tracer (worker50) — exact focus-ack capture race
 * through the REAL drawer preflight (contextPreflight -> applyFocusToThread
 * -> real commitFocusChange -> real ThreadFocusState.select) and REAL store
 * refresh/select actuators. HTTP/auth simulated only, loopback only.
 *
 * RV09 target case A (defect tracer): HOLD the old focus PUT acknowledgement
 * (the server already applied it), observe the application by a REAL refresh,
 * apply a NEW unrelated focus through the native store (real select + real
 * mocked HTTP), then release the EXACT original ack. The OLD pending entry
 * must not send/start using the newer global focus version.
 *
 * Controls: ordered fresh entry stamps its own ack (B), a denied (400) or
 * conflicting (409) focus PUT BLOCKS the entry (C/D). Each case is an
 * independently selectable test.
 */
import { type Page, expect, test } from '@playwright/test';

import type { G2Harness } from '../../playwright/g2-rv0809.js';
import { mockChatFoundation } from './aichat_harness.js';
import { sseBody } from './aichat_harness.js';
import type { MockFocusRow, SSEEvent } from './aichat_harness.js';

declare global {
  interface Window {
    __g2?: G2Harness;
    __g2Errors?: string[];
  }
}

interface CapturedRequest {
  method: string;
  url: string;
  bodyText: string | null;
}

interface FocusMockPlan {
  version: number;
  current: MockFocusRow | null;
  deny: boolean;
  conflict: boolean;
  holdPut: boolean;
  holdConsumed: boolean;
  holdRelease: ((payload: Record<string, unknown>) => void) | null;
}

const OLD_ACK = {
  focus: {
    kind: 'work_order',
    id: 42,
    label: 'WO-0042: G2 work order probe',
    machine_id: null,
    work_order_id: 42
  },
  focus_version: 1
};

/**
 * Canonical complete focus wire rows for the synthetic WorkOrder 42 /
 * kanban card 77 universe: nonempty label with explicit nullable machine_id
 * and work_order_id — the server-resolved truth the stub answers with.
 * Unknown refs resolve to null: no invented labels or authorization.
 */
function canonicalFocusForG2(
  ref: {
    kind?: string;
    id?: number;
  } | null
): MockFocusRow | null {
  if (!ref || typeof ref.kind !== 'string' || typeof ref.id !== 'number') {
    return null;
  }
  if (ref.kind === 'work_order' && ref.id === 42) {
    return {
      kind: 'work_order',
      id: 42,
      label: 'WO-0042: G2 work order probe',
      machine_id: null,
      work_order_id: 42
    };
  }
  if (ref.kind === 'kanban_card' && ref.id === 77) {
    return {
      kind: 'kanban_card',
      id: 77,
      label: 'G2 RV09 probe card 77',
      machine_id: null,
      work_order_id: null
    };
  }
  return null;
}

async function installG2(
  page: Page,
  captured: CapturedRequest[],
  plan: FocusMockPlan
): Promise<void> {
  // Owned pending-route/barrier cleanup: registered before any assertion can
  // strand the held ACK so teardown always releases the pending route.
  let pendingHold: (() => void) | null = null;
  page.on('close', () => {
    const release = pendingHold;
    pendingHold = null;
    if (release) release();
  });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    captured.push({
      method: request.method(),
      url: request.url(),
      bodyText: request.postData()
    });
    if (
      request.method() === 'GET' &&
      /\/kanban\/work-orders\/[^/]+\/overview\/?$/.test(url.pathname)
    ) {
      await route.fulfill({
        json: {
          id: 42,
          reference: 'WO-0042',
          title: 'G2 work order probe',
          description: 'G2 RV09 mounted baseline work order',
          status: 'in_progress',
          priority: 'medium',
          lifecycle_status: 'planned',
          work_order_type: 'corrective',
          machine: 1,
          machine_name: 'Machine One',
          machine_location: 'Plant A',
          assignee: null,
          assigned_to: null,
          assigned_to_name: null,
          scheduled_start: null,
          scheduled_end: null,
          actual_started_at: null,
          actual_completed_at: null,
          due_date: null,
          estimated_minutes: null,
          created_at: '2026-10-01T00:00:00Z',
          updated_at: '2026-10-02T00:00:00Z',
          lifecycle_version: 1,
          canonical_commands_enabled: false,
          cards: [],
          dependencies: [],
          events: [],
          parts: [],
          maintenance_record: null,
          source_alert: null,
          structured_closeout: null,
          repair_packet: {
            id: 7,
            reference: 'RP-0007',
            status: 'draft',
            criticality: 'low',
            fault_summary: '',
            symptom: '',
            production_impact: '',
            generation_status: 'complete',
            diagnosis: {},
            diagnosis_is_preliminary: true,
            diagnosis_status: null,
            findings: [],
            approved_scope: null,
            gates: []
          }
        }
      });
      return;
    }
    if (request.method() === 'GET') {
      await route.fulfill({
        json: { results: [], count: 0, next: null, previous: null }
      });
      return;
    }
    await route.fulfill({ status: 200, json: { ok: true } });
  });
  await mockChatFoundation(page);
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const isFocus = /\/threads\/[^/]+\/focus$/.test(url.pathname);
    if (isFocus && request.method() === 'PUT') {
      const body: {
        focus?: { kind?: string; id?: number } | null;
        expected_version?: number;
      } = request.postDataJSON();
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      });
      if (plan.deny) {
        await route.fulfill({
          status: 400,
          json: { detail: 'focus_update_rejected' }
        });
        return;
      }
      if (plan.conflict) {
        await route.fulfill({
          status: 409,
          json: { detail: 'focus_version_conflict' }
        });
        return;
      }
      // Server APPLIES immediately; the ack may be held by the test.
      // Focus PUT request refs are not canonical ACK rows: resolve them into
      // complete records before answering.
      plan.version += 1;
      plan.current = canonicalFocusForG2(body.focus ?? null);
      // Capture the complete ORIGINAL applied ACK now, before later native
      // GET/select operations mutate plan state. The release closure returns
      // this exact captured ACK and ignores any caller-supplied lookalike.
      const originalAck = {
        focus: plan.current,
        focus_version: plan.version
      };
      if (plan.holdPut && !plan.holdConsumed) {
        plan.holdConsumed = true;
        await new Promise<void>((resolve) => {
          const release = () => {
            if (pendingHold === release) pendingHold = null;
            resolve();
          };
          pendingHold = release;
          plan.holdRelease = () => {
            release();
          };
        });
        try {
          await route.fulfill({ status: 200, json: originalAck });
        } catch {
          // Barrier cleanup released the pending route after page close.
        }
        return;
      }
      await route.fulfill({ status: 200, json: originalAck });
      return;
    }
    if (isFocus && request.method() === 'GET') {
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      });
      await route.fulfill({
        status: 200,
        json: { focus: plan.current, focus_version: plan.version }
      });
      return;
    }
    // Real typed POST is /api/ai/chat/stream (optional trailing slash).
    if (
      request.method() === 'POST' &&
      /\/api\/ai\/chat\/stream\/?$/.test(url.pathname)
    ) {
      const rawBody = request.postData();
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: rawBody
      });
      let parsedBody: { message?: unknown; thread_id?: unknown } = {};
      try {
        parsedBody = JSON.parse(rawBody ?? '{}');
      } catch {
        parsedBody = {};
      }
      const messageText =
        typeof parsedBody.message === 'string' ? parsedBody.message : '';
      const turnThreadId =
        typeof parsedBody.thread_id === 'string' ? parsedBody.thread_id : '';
      if (turnThreadId.length === 0) {
        await route.fulfill({
          status: 400,
          json: { detail: 'thread_id required' }
        });
        return;
      }
      const turnIndex = turnPosts(captured).length;
      const runId = `run-g2-rv09-${turnIndex}`;
      const messageId = `message-g2-rv09-${turnIndex}`;
      const events: SSEEvent[] = [
        { type: 'RUN_STARTED', threadId: turnThreadId, runId },
        {
          type: 'TEXT_MESSAGE_START',
          threadId: turnThreadId,
          runId,
          messageId,
          role: 'assistant'
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          threadId: turnThreadId,
          runId,
          messageId,
          delta: `G2 RV09 acknowledged reply: ${messageText}`
        },
        { type: 'TEXT_MESSAGE_END', threadId: turnThreadId, runId, messageId },
        { type: 'RUN_FINISHED', threadId: turnThreadId, runId }
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody(events)
      });
      return;
    }
    await route.fallback();
  });
  // Loopback-only routing gate: parse the hostname — a lookalike host prefix
  // is never accepted as loopback. Registered LAST so it screens every
  // request before the simulated wire handlers above.
  await page.route(/^https?:\/\//, async (route) => {
    const hostname = new URL(route.request().url()).hostname;
    const loopback =
      hostname === 'localhost' ||
      hostname === '[::1]' ||
      hostname === '::1' ||
      /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(hostname);
    if (!loopback) {
      await route.abort();
      return;
    }
    await route.fallback();
  });
}

function focusPuts(captured: CapturedRequest[]): CapturedRequest[] {
  return captured.filter(
    (entry) =>
      entry.method === 'PUT' && /\/focus$/.test(new URL(entry.url).pathname)
  );
}

function scopePuts(captured: CapturedRequest[]): CapturedRequest[] {
  return captured.filter(
    (entry) =>
      entry.method === 'PUT' && /\/scope$/.test(new URL(entry.url).pathname)
  );
}

function turnPosts(captured: CapturedRequest[]): CapturedRequest[] {
  return captured.filter(
    (entry) =>
      entry.method === 'POST' && entry.url.includes('/api/ai/chat/stream')
  );
}

async function waitForTurn(
  captured: CapturedRequest[],
  text: string,
  timeoutMs: number
): Promise<CapturedRequest> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const match = turnPosts(captured).find((entry) => {
      try {
        const parsed: { message?: unknown } = JSON.parse(
          entry.bodyText ?? '{}'
        );
        return parsed.message === text;
      } catch {
        return false;
      }
    });
    if (match) return match;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`turn with sent text not observed within ${timeoutMs}ms`);
}

async function sendTyped(
  page: Page,
  text: string,
  captured: CapturedRequest[]
): Promise<void> {
  const composer = page.getByTestId('ai-chat-composer').getByRole('textbox');
  await expect(composer).toBeEnabled();
  await composer.fill(text);
  const before = captured.length;
  await composer.press('Enter'); // EXACTLY one submission gesture; no fallback.
  // Held/denied/conflict paths must NOT wait for a successful turn here — the
  // actual test controls the held ACK. Only confirm the single gesture
  // dispatched its request.
  const deadline = Date.now() + 2500;
  while (Date.now() < deadline) {
    if (captured.length > before) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

async function openWorkOrderAsk(page: Page): Promise<void> {
  await page.goto('/playwright/g2-rv0809.html?route=/workorder/42/');
  const ask = page.getByTestId('ask-about-work-order');
  await ask.waitFor({ state: 'visible', timeout: 15000 });
  await ask.click();
  // A pending work_order target renders NO machine routing chip; real
  // drawer/composer readiness is the mounted composer accepting the typed
  // entry (same readiness as the unchanged WorkOrder positive).
  await expect(
    page.getByTestId('ai-chat-composer').getByRole('textbox')
  ).toBeEnabled();
}

const HELD_TEXT = 'G2 RV09 held-ack race probe for work order 42';
const FRESH_TEXT = 'G2 RV09 ordered fresh probe for work order 42';
const DENIED_TEXT = 'G2 RV09 denied focus probe for work order 42';
const CONFLICT_TEXT = 'G2 RV09 conflicting focus probe for work order 42';

test('RV09 held old focus ack: old pending entry must not send using the newer global focus version', async ({
  page
}) => {
  const captured: CapturedRequest[] = [];
  const plan: FocusMockPlan = {
    version: 0,
    current: null,
    deny: false,
    conflict: false,
    holdPut: true,
    holdConsumed: false,
    holdRelease: null
  };
  await installG2(page, captured, plan);
  await openWorkOrderAsk(page);
  await sendTyped(page, HELD_TEXT, captured);

  // 1. Old focus PUT is issued and its ACK is held (server already applied).
  const holdDeadline = Date.now() + 15000;
  while (!plan.holdConsumed && Date.now() < holdDeadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  expect(plan.holdConsumed).toBe(true);
  expect(focusPuts(captured).length).toBe(1);

  // 2. Observe the applied old PUT by a REAL store refresh.
  await page.evaluate(() => window.__g2!.refreshFocus!());
  const afterOld = await page.evaluate(() => window.__g2!.focusState!());
  expect(afterOld.version).toBe(1);

  // 3. Apply a NEW unrelated focus through the native store (real select +
  //    real mocked HTTP); it becomes the NEWER global version (v2).
  await page.evaluate(() =>
    window.__g2!.selectFocus!({ kind: 'kanban_card', id: 77 })
  );
  await page.evaluate(() => window.__g2!.refreshFocus!());
  const afterNew = await page.evaluate(() => window.__g2!.focusState!());
  expect(afterNew.version).toBe(2);
  expect(focusPuts(captured).length).toBe(2);

  // 4. Release the EXACT original ack (v1) late.
  plan.holdRelease!({
    focus: OLD_ACK.focus,
    focus_version: OLD_ACK.focus_version
  });

  // 5. The OLD pending entry resumes. It must NOT send using the newer
  //    global stamp: its turn must carry its OWN ack version (1) or not send.
  const turn = await waitForTurn(captured, HELD_TEXT, 15000);
  expect(turn.bodyText).toContain(HELD_TEXT);
  const turnBody = JSON.parse(turn.bodyText ?? '{}');
  expect(turnBody.expected_focus_version).toBe(OLD_ACK.focus_version);
});

test('RV09 ordered fresh control: entry stamps exactly its own focus ack version', async ({
  page
}) => {
  const captured: CapturedRequest[] = [];
  const plan: FocusMockPlan = {
    version: 0,
    current: null,
    deny: false,
    conflict: false,
    holdPut: false,
    holdConsumed: false,
    holdRelease: null
  };
  await installG2(page, captured, plan);
  await openWorkOrderAsk(page);
  await sendTyped(page, FRESH_TEXT, captured);
  const turn = await waitForTurn(captured, FRESH_TEXT, 15000);
  expect(turn.bodyText).toContain(FRESH_TEXT);
  const puts = focusPuts(captured);
  expect(puts.length).toBe(1);
  expect(scopePuts(captured).length).toBe(0);
  const turnBody = JSON.parse(turn.bodyText ?? '{}');
  expect(turnBody.expected_focus_version).toBe(1);
  // Appended terminal-UI witnesses (all original statements above remain the
  // exact prefix): this entry's OWN unique synthetic reply reached the real
  // UI and the turn is terminally complete — real cancel control absent and
  // the real editor re-eligible — not content visibility alone.
  await expect(
    page.getByText(`G2 RV09 acknowledged reply: ${FRESH_TEXT}`, { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'cancel-ai-chat-turn', exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByTestId('ai-chat-composer').getByRole('textbox')
  ).toBeEnabled();
});

test('RV09 denied focus PUT (400) blocks the typed entry and preserves the draft', async ({
  page
}) => {
  const captured: CapturedRequest[] = [];
  const plan: FocusMockPlan = {
    version: 0,
    current: null,
    deny: true,
    conflict: false,
    holdPut: false,
    holdConsumed: false,
    holdRelease: null
  };
  await installG2(page, captured, plan);
  await openWorkOrderAsk(page);
  await sendTyped(page, DENIED_TEXT, captured);
  await page.waitForTimeout(5000);
  expect(turnPosts(captured).length).toBe(0);
  expect(focusPuts(captured).length).toBe(1);
  const composer = page.getByTestId('ai-chat-composer').getByRole('textbox');
  await expect(composer).toHaveValue(DENIED_TEXT);
});

test('RV09 conflicting focus PUT (409) blocks the typed entry and preserves the draft', async ({
  page
}) => {
  const captured: CapturedRequest[] = [];
  const plan: FocusMockPlan = {
    version: 0,
    current: null,
    deny: false,
    conflict: true,
    holdPut: false,
    holdConsumed: false,
    holdRelease: null
  };
  await installG2(page, captured, plan);
  await openWorkOrderAsk(page);
  await sendTyped(page, CONFLICT_TEXT, captured);
  await page.waitForTimeout(5000);
  expect(turnPosts(captured).length).toBe(0);
  expect(focusPuts(captured).length).toBe(1);
  const composer = page.getByTestId('ai-chat-composer').getByRole('textbox');
  await expect(composer).toHaveValue(CONFLICT_TEXT);
});

// Source-frozen boundary coverage. Native UI New conversation; real stores.
async function hintSnapshot87(page: Page) {
  return await page.evaluate(async (moduleUrl) => {
    const { useAIChatState } = await import(moduleUrl);
    const state = useAIChatState.getState();
    return {
      keys: Object.keys(state),
      targetHint: state.targetHint,
      routingHint: state.routingHint,
      hintThreadId: state.hintThreadId,
      sessionGeneration: state.sessionGeneration,
      isOpen: state.isOpen
    };
  }, '/src/states/AIChatState.tsx');
}

async function newThreadFromNativeMenu87(page: Page): Promise<string> {
  await page
    .getByRole('button', { name: 'select-ai-chat-thread', exact: true })
    .click();
  await page
    .getByRole('menuitem', { name: 'new-ai-chat-thread', exact: true })
    .click();
  await expect
    .poll(async () =>
      page.evaluate(async (moduleUrl) => {
        const { useThreadFocusState } = await import(moduleUrl);
        return useThreadFocusState.getState().binding?.threadId;
      }, '/src/states/ThreadFocusState.ts')
    )
    .not.toBe('thread-playwright-golden');
  const binding = await page.evaluate(async (moduleUrl) => {
    const { useThreadFocusState } = await import(moduleUrl);
    return useThreadFocusState.getState().binding;
  }, '/src/states/ThreadFocusState.ts');
  expect(typeof binding?.threadId).toBe('string');
  expect(binding?.shared).toBe(false);
  if (!binding?.threadId) throw new Error('native new-thread binding absent');
  return binding.threadId;
}

async function installFreshFocusReads87(
  page: Page,
  captured: CapturedRequest[],
  freshAccount: () => boolean = () => false
): Promise<void> {
  await page.route('**/api/ai/threads**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const focus = url.pathname.match(/\/threads\/([^/]+)\/focus$/);
    if (
      request.method() === 'GET' &&
      focus &&
      (decodeURIComponent(focus[1]) !== 'thread-playwright-golden' ||
        freshAccount())
    ) {
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      });
      await route.fulfill({ json: { focus: null, focus_version: 0 } });
      return;
    }
    await route.fallback();
  });
}

async function expectHintCleared87(page: Page): Promise<void> {
  const state = await hintSnapshot87(page);
  expect(state.keys).toEqual(
    expect.arrayContaining(['targetHint', 'routingHint', 'hintThreadId'])
  );
  expect(state.targetHint).toBeUndefined();
  expect(state.routingHint).toBeUndefined();
  expect(state.hintThreadId).toBeNull();
}

async function expectFreshTerminal87(page: Page, text: string): Promise<void> {
  await expect(
    page.getByText(`G2 RV09 acknowledged reply: ${text}`, { exact: true })
  ).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'cancel-ai-chat-turn', exact: true })
  ).toHaveCount(0);
  await expect(
    page.getByTestId('ai-chat-composer').getByRole('textbox')
  ).toBeEnabled();
}

test('RV09 held old focus ack cannot cross a real new-thread boundary', async ({
  page
}) => {
  const captured: CapturedRequest[] = [];
  const plan: FocusMockPlan = {
    version: 0,
    current: null,
    deny: false,
    conflict: false,
    holdPut: true,
    holdConsumed: false,
    holdRelease: null
  };
  await installG2(page, captured, plan);
  await installFreshFocusReads87(page, captured);
  await openWorkOrderAsk(page); // The real WorkOrder Ask is the only hint source.
  await expect
    .poll(async () => (await hintSnapshot87(page)).hintThreadId)
    .toBe('thread-playwright-golden');
  expect((await hintSnapshot87(page)).targetHint).toEqual({
    kind: 'work_order',
    id: 42,
    label: 'WO-0042: G2 work order probe'
  });
  const oldText =
    'Old thread held ACK must never dispatch across the selection boundary.';
  await sendTyped(page, oldText, captured);
  await expect.poll(() => plan.holdConsumed).toBe(true);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(turnPosts(captured)).toHaveLength(0);
  const nextThread = await newThreadFromNativeMenu87(page);
  await expectHintCleared87(page);
  await expect
    .poll(
      async () => (await page.evaluate(() => window.__g2!.focusState())).version
    )
    .toBe(0);
  plan.holdRelease!({}); // The existing handler returns its captured original ACK.
  await page.waitForTimeout(1500);
  expect(turnPosts(captured)).toHaveLength(0);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(await page.evaluate(() => window.__g2!.focusState())).toEqual({
    version: 0,
    focusLabel: null
  });
  await expectHintCleared87(page);
  const freshText =
    'New thread fresh entry remains eligible without the old WorkOrder hint.';
  await sendTyped(page, freshText, captured);
  const turn = await waitForTurn(captured, freshText, 15000);
  const body = JSON.parse(turn.bodyText ?? '{}');
  expect(body.message).toBe(freshText);
  expect(body.thread_id).toBe(nextThread);
  expect(body.expected_focus_version).toBe(0);
  expect(turnPosts(captured)).toHaveLength(1);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(scopePuts(captured)).toHaveLength(0);
  await expectFreshTerminal87(page, freshText);
});

test('RV09 held old focus ack cannot cross a real account-store boundary', async ({
  page
}) => {
  const captured: CapturedRequest[] = [];
  const plan: FocusMockPlan = {
    version: 0,
    current: null,
    deny: false,
    conflict: false,
    holdPut: true,
    holdConsumed: false,
    holdRelease: null
  };
  let switchedAccount = false;
  const freshAccountReads: string[] = [];
  await installG2(page, captured, plan);
  await installFreshFocusReads87(page, captured, () => switchedAccount);
  await page.route('**/api/ai/threads**', async (route) => {
    if (
      switchedAccount &&
      route.request().method() === 'GET' &&
      /\/threads\/[^/]+\/focus$/.test(new URL(route.request().url()).pathname)
    ) {
      freshAccountReads.push(route.request().url());
    }
    await route.fallback();
  });
  await openWorkOrderAsk(page);
  await expect
    .poll(async () => (await hintSnapshot87(page)).hintThreadId)
    .toBe('thread-playwright-golden');
  const before = await hintSnapshot87(page);
  expect(before.targetHint?.id).toBe(42);
  const oldText =
    'Old account held ACK must never dispatch in the replacement account.';
  await sendTyped(page, oldText, captured);
  await expect.poll(() => plan.holdConsumed).toBe(true);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(turnPosts(captured)).toHaveLength(0);
  switchedAccount = true;
  const actor = await page.evaluate(async (moduleUrl) => {
    const { useUserState } = await import(moduleUrl);
    const state = useUserState.getState();
    const previous = state.getUser();
    if (!previous) throw new Error('ordinary source actor absent');
    state.setUser({
      ...previous,
      pk: 22,
      username: 'g2 second ordinary actor',
      is_staff: false,
      is_superuser: false
    });
    const next = useUserState.getState().getUser();
    return {
      id: useUserState.getState().userId(),
      staff: next?.is_staff,
      superuser: next?.is_superuser,
      groups: next?.groups
    };
  }, '/src/states/UserState.tsx');
  expect(actor).toEqual({ id: 22, staff: false, superuser: false, groups: [] });
  expect(await page.evaluate(() => window.__g2!.accountUserId())).toBe(22);
  await expect
    .poll(async () => (await hintSnapshot87(page)).sessionGeneration)
    .toBeGreaterThan(before.sessionGeneration);
  await expectHintCleared87(page);
  // The real account reset closes the drawer. Reopen unscoped, with no
  // invented Ask/hint: this is the actual native store's global-open action.
  await page.evaluate(async (moduleUrl) => {
    const { useAIChatState } = await import(moduleUrl);
    useAIChatState.getState().open();
  }, '/src/states/AIChatState.tsx');
  await expect.poll(() => freshAccountReads.length).toBeGreaterThan(0);
  await expect
    .poll(
      async () => (await page.evaluate(() => window.__g2!.focusState())).version
    )
    .toBe(0);
  plan.holdRelease!({}); // Only the captured old-account PUT is released.
  await page.waitForTimeout(1500);
  expect(turnPosts(captured)).toHaveLength(0);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(await page.evaluate(() => window.__g2!.focusState())).toEqual({
    version: 0,
    focusLabel: null
  });
  await expectHintCleared87(page);
  const freshText =
    'Replacement ordinary account can send without the old WorkOrder hint.';
  await sendTyped(page, freshText, captured);
  const turn = await waitForTurn(captured, freshText, 15000);
  const body = JSON.parse(turn.bodyText ?? '{}');
  expect(body.message).toBe(freshText);
  expect(body.expected_focus_version).toBe(0);
  expect(turnPosts(captured)).toHaveLength(1);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(scopePuts(captured)).toHaveLength(0);
  expect(await page.evaluate(() => window.__g2!.accountUserId())).toBe(22);
  await expectFreshTerminal87(page, freshText);
});

// Parent91 carrier-only mounted voice consumer proof. Full real drawer,
// VoiceSessionState singleton and callbacks; only loopback HTTP/device traps.
declare global {
  interface Window {
    __rv09VoicePending91?: Promise<void>;
    __rv09MicCalls91?: number;
  }
}
const voiceCapability91 = {
  enabled: true,
  foreground_session: true,
  decisions: true,
  prompts: true,
  help: true,
  presentation: true,
  mobile_surface: true,
  wake_lock: false,
  ice_servers: [],
  modes: ['continuous', 'push_to_talk'],
  default_mode: 'continuous',
  max_queued_turns: 2,
  idle_timeout_s: 300,
  mic_silence_rms: 0.01,
  mic_silence_window_s: 1.5,
  route_loss_pause: true,
  tts_timeout_s: 8,
  decision_max_armed_s: 300,
  reconnect: { grace_s: 3, max_attempts: 2 },
  confidence_floor: 0.85,
  locales: ['en-US'],
  voices: [{ locale: 'en-US', voice: 'en-US-AvaNeural' }],
  default_locale: 'en-US',
  default_voice: 'en-US-AvaNeural',
  consent_version: 'consent-v3-memory',
  voice_write_locales: ['en-US']
};

async function mountedVoiceAck91(page: Page, held: boolean): Promise<void> {
  const captured: CapturedRequest[] = [];
  const voicePosts: CapturedRequest[] = [];
  const plan: FocusMockPlan = {
    version: 0,
    current: null,
    deny: false,
    conflict: false,
    holdPut: held,
    holdConsumed: false,
    holdRelease: null
  };
  await installG2(page, captured, plan);
  await page.route('**/api/ai/voice/sessions', async (route) => {
    const request = route.request();
    expect(new URL(request.url()).hostname).toBe('127.0.0.1');
    expect(request.method()).toBe('POST');
    voicePosts.push({
      method: request.method(),
      url: request.url(),
      bodyText: request.postData()
    });
    // Deliberately refuse at the server boundary in BOTH cases. This lane
    // proves the real mounted request writer; it cannot touch media/provider.
    await route.fulfill({
      status: 409,
      json: { detail: 'VOICE_FOCUS_CHANGED' }
    });
  });
  await openWorkOrderAsk(page);
  await page.evaluate(
    async ({ voiceUrl, prefsUrl, focusUrl, capability }) => {
      const { voiceController } = await import(voiceUrl);
      const { useLocalState } = await import(prefsUrl);
      const { useThreadFocusState } = await import(focusUrl);
      const binding = useThreadFocusState.getState().binding;
      if (!binding?.threadId)
        throw new Error('actual mounted owned binding missing');
      window.__rv09MicCalls91 = 0;
      navigator.mediaDevices.getUserMedia = async () => {
        window.__rv09MicCalls91 = (window.__rv09MicCalls91 ?? 0) + 1;
        throw new Error('REAL DEVICE CALL FORBIDDEN in mounted ACK probe');
      };
      useLocalState.getState().setVoicePreferences({
        voiceConsentVersion: capability.consent_version,
        voiceLocale: capability.default_locale,
        voiceOutputVoice: capability.default_voice
      });
      voiceController.configure(
        `${window.location.origin}/api/ai`,
        binding.threadId
      );
      voiceController.setCapability(capability);
      window.__rv09VoicePending91 = voiceController.start();
    },
    {
      voiceUrl: '/src/states/VoiceSessionState.tsx',
      prefsUrl: '/src/states/LocalState.tsx',
      focusUrl: '/src/states/ThreadFocusState.ts',
      capability: voiceCapability91
    }
  );
  try {
    await expect.poll(() => focusPuts(captured).length).toBe(1);
    if (held) {
      expect(plan.holdConsumed).toBe(true);
      await page.evaluate(() => window.__g2!.refreshFocus!());
      expect(
        (await page.evaluate(() => window.__g2!.focusState!())).version
      ).toBe(1);
      await page.evaluate(() =>
        window.__g2!.selectFocus!({ kind: 'kanban_card', id: 77 })
      );
      await page.evaluate(() => window.__g2!.refreshFocus!());
      expect(
        (await page.evaluate(() => window.__g2!.focusState!())).version
      ).toBe(2);
      expect(focusPuts(captured)).toHaveLength(2);
      expect(voicePosts).toHaveLength(0);
      plan.holdRelease!({
        focus: OLD_ACK.focus,
        focus_version: OLD_ACK.focus_version
      });
    }
    await page.evaluate(() => window.__rv09VoicePending91);
    expect(await page.evaluate(() => window.__rv09MicCalls91)).toBe(0);
    expect(scopePuts(captured)).toHaveLength(0);
    expect(turnPosts(captured)).toHaveLength(0);
    if (!held) expect(voicePosts).toHaveLength(1); // Observable mounted positive.
    const stamps = voicePosts.map(
      (entry) => JSON.parse(entry.bodyText ?? '{}').expected_focus_version
    );
    console.log(
      'RV09_MOUNTED91',
      JSON.stringify({ held, stamps, count: voicePosts.length })
    );
    if (held) expect(stamps).not.toContain(2);
    else expect(stamps).toEqual([1]);
  } finally {
    plan.holdRelease?.({});
    await page.evaluate(() => window.__rv09VoicePending91).catch(() => {});
  }
}

test('RV09 mounted voice ordered control writes its own ACK1 before simulated refusal', async ({
  page
}) => {
  await mountedVoiceAck91(page, false);
});
test('RV09 mounted voice held old ACK1 cannot write newer global stamp2', async ({
  page
}) => {
  await mountedVoiceAck91(page, true);
});
