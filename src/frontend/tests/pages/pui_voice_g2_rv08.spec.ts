/**
 * G2 RV08 mounted baseline tracer (worker50) — REAL Ask producer through the
 * real drawer/preflight/stores. WorkOrderDetail's real
 * data-testid=ask-about-work-order button is the ONLY Ask entry (no copied
 * button, no direct store.openWithTargetHint call). HTTP/auth simulated only,
 * loopback only, non-loopback requests aborted.
 *
 * RV08 targets exercised here (independently selectable cases):
 *  - first entry: exactly one work_order focus PUT with the exact canonical
 *    WorkOrder PK, ZERO scope PUTs, byte-identical sent text, entry stamps
 *    its own focus ack version;
 *  - second send: must NOT reapply the original hint (no second focus PUT,
 *    no stale pending hint after the first send).
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

const workOrderOverviewPayload = {
  id: 42,
  reference: 'WO-0042',
  title: 'G2 work order probe',
  description: 'G2 RV08 mounted baseline work order',
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
};

/**
 * Canonical complete focus wire rows for the synthetic WorkOrder 42 /
 * Machine 1 universe: nonempty label with explicit nullable machine_id /
 * work_order_id — the server-resolved truth the stub answers with.
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
  if (ref.kind === 'machine' && ref.id === 1) {
    return {
      kind: 'machine',
      id: 1,
      label: 'Machine One',
      machine_id: 1,
      work_order_id: null
    };
  }
  return {
    kind: ref.kind,
    id: ref.id,
    label: `${ref.kind} ${ref.id}`,
    machine_id: ref.kind === 'machine' ? ref.id : null,
    work_order_id: ref.kind === 'work_order' ? ref.id : null
  };
}

async function installG2(
  page: Page,
  captured: CapturedRequest[],
  plan: FocusMockPlan
): Promise<void> {
  // Abort EVERY non-loopback request (no provider/telemetry/external calls).
  await page.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, async (route) => {
    await route.abort();
  });
  // Unknown API reads: synthetic empty collections (HTTP simulation only).
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
      await route.fulfill({ json: workOrderOverviewPayload });
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
  // Frozen native chat foundation wire shapes (threads/capabilities/scope).
  await mockChatFoundation(page);
  // Override LAST (wins): exact focus PUT/GET semantics + turn capture.
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
      plan.version += 1;
      plan.current = canonicalFocusForG2(body.focus ?? null);
      const ack = {
        focus: plan.current,
        focus_version: plan.version
      };
      if (plan.holdPut && !plan.holdConsumed) {
        plan.holdConsumed = true;
        const released: Record<string, unknown> = await new Promise(
          (resolve) => {
            plan.holdRelease = resolve;
          }
        );
        await route.fulfill({ status: 200, json: released });
        return;
      }
      await route.fulfill({ status: 200, json: ack });
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
      let parsedBody: { thread_id?: unknown } = {};
      try {
        parsedBody = JSON.parse(rawBody ?? '{}');
      } catch {
        parsedBody = {};
      }
      const turnThreadId =
        typeof parsedBody.thread_id === 'string' &&
        parsedBody.thread_id.length > 0
          ? parsedBody.thread_id
          : 'g2-rv08-thread';
      const turnIndex = turnPosts(captured).length;
      const runId = `run-g2-rv08-${turnIndex}`;
      const messageId = `message-g2-rv08-${turnIndex}`;
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
          delta: `G2 RV08 acknowledged reply for work order 42 [turn ${turnIndex}]`
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
  const countBefore = turnPosts(captured).length;
  await expect(composer).toBeEnabled();
  await composer.fill(text);
  await composer.press('Enter'); // EXACTLY one user action; no fallback click.
  const turn = await waitForTurn(captured, text, 15000);
  expect(turnPosts(captured).length).toBe(countBefore + 1);
  const body = JSON.parse(turn.bodyText ?? '{}');
  expect(body.message).toBe(text);
  expect(typeof body.thread_id).toBe('string');
  expect(body.thread_id.length).toBeGreaterThan(0);
  // Content is not terminal truth: pair the per-turn reply witness with
  // real cancellation-control absence and real editor re-eligibility.
  await expect(
    page.getByText(
      `G2 RV08 acknowledged reply for work order 42 [turn ${countBefore + 1}]`,
      { exact: true }
    )
  ).toBeVisible();
  await expect(
    page.getByRole('button', {
      name: 'cancel-ai-chat-turn',
      exact: true
    })
  ).toHaveCount(0);
  await expect(composer).toBeEnabled();
}

const FIRST_TEXT = 'G2 RV08 first-entry probe for work order 42';
const SECOND_TEXT = 'G2 RV08 second-send probe for work order 42';

test('RV08 work-order Ask first entry: one exact work_order focus PUT, no scope PUT, byte-identical send, own-ack stamp', async ({
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
  await page.goto('/playwright/g2-rv0809.html?route=/workorder/42/');
  const ask = page.getByTestId('ask-about-work-order');
  await ask.waitFor({ state: 'visible', timeout: 15000 });
  await ask.click();
  const drawer = await page.evaluate(() => window.__g2!.drawerState());
  expect(drawer.isOpen).toBe(true);
  expect(drawer.targetHint?.kind).toBe('work_order');
  expect(drawer.targetHint?.id).toBe(42);
  // A work_order hint renders NO machine routing chip; real pre-send
  // readiness is the mounted composer accepting the exact typed entry.
  await expect(
    page.getByTestId('ai-chat-composer').getByRole('textbox')
  ).toBeEnabled();

  await sendTyped(page, FIRST_TEXT, captured);
  const turn = await waitForTurn(captured, FIRST_TEXT, 15000);
  // Byte-identical sent words, no scope widening.
  expect(turn.bodyText).toContain(FIRST_TEXT);
  const turnBody = JSON.parse(turn.bodyText ?? '{}');
  expect(turnBody.message).toBe(FIRST_TEXT);
  expect(typeof turnBody.thread_id).toBe('string');
  // Native first-send completion: the acknowledged reply reaches the UI.
  await expect(
    page.getByText('G2 RV08 acknowledged reply for work order 42')
  ).toBeVisible();

  const puts = focusPuts(captured);
  expect(puts.length).toBe(1);
  const focusBody = JSON.parse(puts[0].bodyText ?? '{}');
  expect(focusBody.focus).toEqual({ kind: 'work_order', id: 42 });
  expect(scopePuts(captured).length).toBe(0);

  // The entry stamps its OWN focus ack version (v1), never a newer global.
  expect(turnBody.expected_focus_version).toBe(1);
});

test('RV08 second typed send must not reapply the original hint (no repeated focus PUT, no stale pending hint)', async ({
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
  await page.goto('/playwright/g2-rv0809.html?route=/workorder/42/');
  const ask = page.getByTestId('ask-about-work-order');
  await ask.waitFor({ state: 'visible', timeout: 15000 });
  await ask.click();
  // Real drawer/input readiness (a work_order hint renders NO routing chip).
  await expect(
    page.getByTestId('ai-chat-composer').getByRole('textbox')
  ).toBeEnabled();

  await sendTyped(page, FIRST_TEXT, captured);
  await waitForTurn(captured, FIRST_TEXT, 15000);
  // Real streaming completion is observable before the second send.
  await expect(
    page.getByText('G2 RV08 acknowledged reply for work order 42')
  ).toBeVisible();

  await sendTyped(page, SECOND_TEXT, captured);
  await waitForTurn(captured, SECOND_TEXT, 15000);
  // Settle any late preflight mutation before counting.
  await page.waitForTimeout(1500);

  const puts = focusPuts(captured);
  expect(puts.length).toBe(1);
  const secondTurn = turnPosts(captured).find((entry) =>
    (entry.bodyText ?? '').includes(SECOND_TEXT)
  );
  expect(secondTurn).toBeTruthy();
  const secondBody = JSON.parse(secondTurn?.bodyText ?? '{}');
  expect(secondBody.message).toBe(SECOND_TEXT);
  expect(secondBody.expected_focus_version).toBe(1);
  const firstBody = JSON.parse(turnPosts(captured)[0]?.bodyText ?? '{}');
  expect(secondBody.thread_id).toBe(firstBody.thread_id);
});

test('RV08 work-order hint is consumed after one acknowledged typed entry', async ({
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
  await page.goto('/playwright/g2-rv0809.html?route=/workorder/42/');
  const ask = page.getByTestId('ask-about-work-order');
  await ask.waitFor({ state: 'visible', timeout: 15000 });
  await ask.click();
  // REAL pending target: a nonempty work_order/42 hint is held open.
  const pending = await page.evaluate(() => window.__g2!.drawerState());
  expect(pending.isOpen).toBe(true);
  expect(pending.targetHint?.kind).toBe('work_order');
  expect(pending.targetHint?.id).toBe(42);

  await sendTyped(page, FIRST_TEXT, captured);
  await waitForTurn(captured, FIRST_TEXT, 15000);
  // Valid native completion first: the acknowledged reply reaches the UI.
  await expect(
    page.getByText('G2 RV08 acknowledged reply for work order 42')
  ).toBeVisible();

  // The acknowledged typed entry must CONSUME the pending work-order hint.
  const after = await page.evaluate(() => window.__g2!.drawerState());
  expect(after.targetHint).toBeUndefined();
});

// Parent64 frozen-source challenge: real server-detail shared flag, real Ask.
test('RV08 shared WorkOrder Ask retains its exact target across the native owned-thread hop', async ({
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
  const sharedThread = 'thread-playwright-golden';
  let servedSharedDetail = 0;
  await installG2(page, captured, plan);
  await page.route('**/api/ai/threads**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
      await route.abort('blockedbyclient');
      return;
    }
    if (
      request.method() === 'GET' &&
      url.pathname === `/api/ai/threads/${sharedThread}`
    ) {
      await route.fulfill({
        json: {
          thread_id: sharedThread,
          title: 'Server conversation',
          summary: 'Server conversation',
          created_at: '2026-07-15T00:00:00Z',
          updated_at: '2026-07-15T00:01:00Z',
          active_scope: null,
          shared: true,
          messages: []
        }
      });
      servedSharedDetail += 1;
      return;
    }
    await route.fallback();
  });
  await page.goto('/playwright/g2-rv0809.html?route=/workorder/42/');
  await expect.poll(() => servedSharedDetail).toBeGreaterThan(0);
  await expect
    .poll(
      () =>
        captured.filter(
          (entry) =>
            entry.method === 'GET' &&
            new URL(entry.url).pathname ===
              `/api/ai/threads/${sharedThread}/focus`
        ).length
    )
    .toBeGreaterThan(0);
  await expect
    .poll(async () =>
      page.evaluate(async (moduleUrl) => {
        const { useThreadFocusState } = await import(moduleUrl);
        const binding = useThreadFocusState.getState().binding;
        return { threadId: binding?.threadId, shared: binding?.shared };
      }, '/src/states/ThreadFocusState.ts')
    )
    .toEqual({ threadId: sharedThread, shared: true });
  const focusReadOffset = captured.length;
  const ask = page.getByTestId('ask-about-work-order');
  await expect(ask).toBeVisible();
  await ask.click(); // one genuine WorkOrder Ask, no store write from the probe.
  // The new focus GET is a real native new-owned-thread witness, not only
  // disappearance of the read-only warning or a direct private hook call.
  await expect
    .poll(
      () =>
        captured.slice(focusReadOffset).filter((entry) => {
          const pathname = new URL(entry.url).pathname;
          return (
            entry.method === 'GET' &&
            /\/threads\/[^/]+\/focus$/.test(pathname) &&
            pathname !== `/api/ai/threads/${sharedThread}/focus`
          );
        }).length
    )
    .toBeGreaterThan(0);
  await expect(
    page.getByTestId('ai-chat-composer').getByRole('textbox')
  ).toBeEnabled();
  const state = await page.evaluate(() => window.__g2!.drawerState());
  expect(state.isOpen).toBe(true);
  expect(state.targetHint).toEqual({
    kind: 'work_order',
    id: 42,
    label: 'WO-0042: G2 work order probe'
  });
  expect(typeof state.hintThreadId).toBe('string');
  expect(state.hintThreadId).not.toBe(sharedThread);
  expect(focusPuts(captured)).toHaveLength(0);
  expect(scopePuts(captured)).toHaveLength(0);
  expect(turnPosts(captured)).toHaveLength(0);
  const ownedBinding = await page.evaluate(async (moduleUrl) => {
    const { useThreadFocusState } = await import(moduleUrl);
    const binding = useThreadFocusState.getState().binding;
    return { threadId: binding?.threadId, shared: binding?.shared };
  }, '/src/states/ThreadFocusState.ts');
  expect(ownedBinding.shared).toBe(false);
  expect(state.hintThreadId).toBe(ownedBinding.threadId);
  const text =
    'Shared WorkOrder42: bytes, focus owner and one acknowledged consume.';
  await sendTyped(page, text, captured);
  const puts = focusPuts(captured);
  expect(puts).toHaveLength(1);
  expect(JSON.parse(puts[0].bodyText ?? '{}')).toEqual({
    focus: { kind: 'work_order', id: 42 },
    expected_focus_version: 0
  });
  expect(scopePuts(captured)).toHaveLength(0);
  expect(turnPosts(captured)).toHaveLength(1);
  const body = JSON.parse(turnPosts(captured)[0].bodyText ?? '{}');
  expect(body.thread_id).toBe(ownedBinding.threadId);
  expect(body.message).toBe(text);
  expect(body.expected_focus_version).toBe(1);
  await expect(
    page.getByText('G2 RV08 acknowledged reply for work order 42 [turn 1]', {
      exact: true
    })
  ).toBeVisible();
  const consumed = await page.evaluate(async (moduleUrl) => {
    const { useAIChatState } = await import(moduleUrl);
    const state = useAIChatState.getState();
    return {
      targetHint: state.targetHint,
      routingHint: state.routingHint,
      hintThreadId: state.hintThreadId
    };
  }, '/src/states/AIChatState.tsx');
  expect(consumed.targetHint).toBeUndefined();
  expect(consumed.routingHint).toBeUndefined();
  expect(consumed.hintThreadId).toBeNull();
});

// Additional frozen-production coverage. Real MachineDetail Ask; mocked HTTP only.
async function sendMachineTyped78(
  page: Page,
  text: string,
  captured: CapturedRequest[]
): Promise<void> {
  const composer = page.getByTestId('ai-chat-composer').getByRole('textbox');
  const count = turnPosts(captured).length;
  await expect(composer).toBeEnabled();
  await composer.fill(text);
  await composer.press('Enter'); // One gesture. Never retry through Send.
  await waitForTurn(captured, text, 15000);
  expect(turnPosts(captured)).toHaveLength(count + 1);
  await expect(
    page.getByText(
      `G2 RV08 acknowledged Machine One reply [turn ${count + 1}]`,
      { exact: true }
    )
  ).toBeVisible();
  await expect(
    page.getByRole('button', {
      name: 'cancel-ai-chat-turn',
      exact: true
    })
  ).toHaveCount(0);
  await expect(composer).toBeEnabled();
}

async function runMachineHint78(page: Page, shared: boolean): Promise<void> {
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
  const originalThread = 'thread-playwright-golden';
  let servedSharedDetail = 0;
  let scopeVersion = 0;
  let scopeMode = 'legacy_unconfirmed';
  let machineIds: number[] = [];
  let displayLabel = '';
  await installG2(page, captured, plan);
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
      await route.abort('blockedbyclient');
      return;
    }
    if (
      request.method() === 'GET' &&
      url.pathname === '/api/assets/machines/1/'
    ) {
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      });
      await route.fulfill({
        json: {
          pk: 1,
          name: 'Machine One',
          description: 'Synthetic Machine One fixture',
          active: true,
          location: 'Plant A',
          manufacturer: '',
          model: '',
          serial: '',
          barcode_hash: null
        }
      });
      return;
    }
    if (
      shared &&
      request.method() === 'GET' &&
      url.pathname === `/api/ai/threads/${originalThread}`
    ) {
      await route.fulfill({
        json: {
          thread_id: originalThread,
          title: 'Server conversation',
          summary: 'Server conversation',
          created_at: '2026-07-15T00:00:00Z',
          updated_at: '2026-07-15T00:01:00Z',
          active_scope: null,
          shared: true,
          messages: []
        }
      });
      servedSharedDetail += 1;
      return;
    }
    const scopeMatch = url.pathname.match(/\/threads\/([^/]+)\/scope$/);
    if (scopeMatch) {
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      });
      if (request.method() === 'PUT') {
        const body: {
          scope: { mode: string; machine_ids: number[]; display_label: string };
        } = request.postDataJSON();
        scopeVersion += 1;
        scopeMode = body.scope.mode;
        machineIds = body.scope.machine_ids;
        displayLabel = body.scope.display_label;
      }
      await route.fulfill({
        json: {
          thread_id: decodeURIComponent(scopeMatch[1]),
          scope: {
            schema_version: 1,
            mode: scopeMode,
            machine_ids: machineIds,
            date_window: { from: null, to: null },
            source_classes: [
              'controlled_document',
              'asset_attachment',
              'work_order',
              'maintenance_record'
            ],
            display_label: displayLabel
          },
          version: scopeVersion,
          hash: scopeVersion > 0 ? 'f'.repeat(64) : '',
          display_label: displayLabel,
          editable: true
        }
      });
      return;
    }
    if (
      request.method() === 'POST' &&
      /\/api\/ai\/chat\/stream\/?$/.test(url.pathname)
    ) {
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      });
      const body: { thread_id: string } = request.postDataJSON();
      const index = turnPosts(captured).length;
      const runId = `run-machine78-${index}`;
      const messageId = `message-machine78-${index}`;
      const events: SSEEvent[] = [
        { type: 'RUN_STARTED', threadId: body.thread_id, runId },
        {
          type: 'TEXT_MESSAGE_START',
          threadId: body.thread_id,
          runId,
          messageId,
          role: 'assistant'
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          threadId: body.thread_id,
          runId,
          messageId,
          delta: `G2 RV08 acknowledged Machine One reply [turn ${index}]`
        },
        {
          type: 'TEXT_MESSAGE_END',
          threadId: body.thread_id,
          runId,
          messageId
        },
        { type: 'RUN_FINISHED', threadId: body.thread_id, runId }
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
  await page.goto('/playwright/g2-rv0809.html?route=/machine/1/');
  await expect
    .poll(async () =>
      page.evaluate(async (moduleUrl) => {
        const { useThreadFocusState } = await import(moduleUrl);
        const binding = useThreadFocusState.getState().binding;
        return { threadId: binding?.threadId, shared: binding?.shared };
      }, '/src/states/ThreadFocusState.ts')
    )
    .toEqual({ threadId: originalThread, shared });
  if (shared) expect(servedSharedDetail).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.__g2!.accountUserId())).toBe(11);
  const offset = captured.length;
  const ask = page.getByRole('button', {
    name: 'Ask about this machine',
    exact: true
  });
  const observedRoute = await page.evaluate(() => ({
    url: window.location.href,
    route: new URLSearchParams(window.location.search).get('route'),
    body: document.body.innerText.slice(0, 1200),
    buttons: Array.from(document.querySelectorAll('button')).map(
      (button) => button.textContent
    ),
    errors: window.__g2Errors ?? []
  }));
  await expect(
    ask,
    JSON.stringify({
      observedRoute,
      requests: captured.map((entry) => ({
        method: entry.method,
        url: entry.url
      }))
    })
  ).toBeEnabled();
  await ask.click(); // The real MachineDetail producer is the only Ask action.
  if (shared) {
    await expect
      .poll(
        () =>
          captured
            .slice(offset)
            .filter(
              (entry) =>
                entry.method === 'GET' &&
                /\/threads\/[^/]+\/focus$/.test(new URL(entry.url).pathname) &&
                new URL(entry.url).pathname !==
                  `/api/ai/threads/${originalThread}/focus`
            ).length
      )
      .toBeGreaterThan(0);
  }
  await expect(
    page.getByTestId('ai-chat-composer').getByRole('textbox')
  ).toBeEnabled();
  const pending = await page.evaluate(async (moduleUrl) => {
    const { useAIChatState } = await import(moduleUrl);
    const state = useAIChatState.getState();
    return {
      isOpen: state.isOpen,
      targetHint: state.targetHint,
      routingHint: state.routingHint,
      hintThreadId: state.hintThreadId
    };
  }, '/src/states/AIChatState.tsx');
  expect(pending.isOpen).toBe(true);
  expect(pending.targetHint).toEqual({
    kind: 'machine',
    id: 1,
    label: 'Machine One',
    machineId: 1
  });
  expect(pending.routingHint).toEqual({
    machineId: 1,
    machineName: 'Machine One'
  });
  const binding = await page.evaluate(async (moduleUrl) => {
    const { useThreadFocusState } = await import(moduleUrl);
    const value = useThreadFocusState.getState().binding;
    return { threadId: value?.threadId, shared: value?.shared };
  }, '/src/states/ThreadFocusState.ts');
  expect(binding.shared).toBe(false);
  expect(pending.hintThreadId).toBe(binding.threadId);
  if (shared) expect(binding.threadId).not.toBe(originalThread);
  else expect(binding.threadId).toBe(originalThread);
  expect(focusPuts(captured)).toHaveLength(0);
  expect(scopePuts(captured)).toHaveLength(0);
  expect(turnPosts(captured)).toHaveLength(0);
  const first = `Machine One ${shared ? 'shared' : 'owned'} first: exact bytes Ω.\nNo injected context.`;
  const second = `Machine One ${shared ? 'shared' : 'owned'} second: preserve scope, focus and bytes.`;
  await sendMachineTyped78(page, first, captured);
  expect(scopePuts(captured)).toHaveLength(1);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(JSON.parse(scopePuts(captured)[0].bodyText ?? '{}')).toEqual({
    expected_version: 0,
    scope: {
      mode: 'explicit_assets',
      machine_ids: [1],
      display_label: 'Machine One'
    }
  });
  expect(JSON.parse(focusPuts(captured)[0].bodyText ?? '{}')).toEqual({
    focus: { kind: 'machine', id: 1 },
    expected_focus_version: 0
  });
  const scopeIndex = captured.indexOf(scopePuts(captured)[0]);
  const focusIndex = captured.indexOf(focusPuts(captured)[0]);
  const turnIndex = captured.indexOf(turnPosts(captured)[0]);
  expect(scopeIndex).toBeLessThan(focusIndex);
  expect(focusIndex).toBeLessThan(turnIndex);
  const consumed = await page.evaluate(async (moduleUrl) => {
    const { useAIChatState } = await import(moduleUrl);
    const state = useAIChatState.getState();
    return {
      targetHint: state.targetHint,
      routingHint: state.routingHint,
      hintThreadId: state.hintThreadId
    };
  }, '/src/states/AIChatState.tsx');
  expect(consumed.targetHint).toBeUndefined();
  expect(consumed.routingHint).toBeUndefined();
  expect(consumed.hintThreadId).toBeNull();
  await sendMachineTyped78(page, second, captured);
  await page.waitForTimeout(1500);
  expect(turnPosts(captured)).toHaveLength(2);
  expect(scopePuts(captured)).toHaveLength(1);
  expect(focusPuts(captured)).toHaveLength(1);
  for (const [index, text] of [first, second].entries()) {
    const body = JSON.parse(turnPosts(captured)[index].bodyText ?? '{}');
    expect(body.thread_id).toBe(binding.threadId);
    expect(body.message).toBe(text);
    expect(body.expected_focus_version).toBe(1);
    expect(body.expected_scope_version).toBe(1);
  }
  expect(new URL(scopePuts(captured)[0].url).pathname).toBe(
    `/api/ai/threads/${binding.threadId}/scope`
  );
  expect(new URL(focusPuts(captured)[0].url).pathname).toBe(
    `/api/ai/threads/${binding.threadId}/focus`
  );
}

test('RV08 owned Machine Ask commits canonical scope and focus once', async ({
  page
}) => {
  await runMachineHint78(page, false);
});

test('RV08 shared Machine Ask commits canonical scope and focus once', async ({
  page
}) => {
  await runMachineHint78(page, true);
});

// Frozen-production coverage: real MaintenanceBoard edit-modal Task Ask.
import type { BoardCard, WorkOrder } from '../../lib/types/Tasks.js';

const TASK_TITLE83 = 'Task 77 native contextual focus';
const TASK_DRAFT83 = 'Unsaved Task 77 title must survive Ask';

async function sendTaskTyped83(
  page: Page,
  text: string,
  captured: CapturedRequest[]
): Promise<void> {
  const composer = page.getByTestId('ai-chat-composer').getByRole('textbox');
  const before = turnPosts(captured).length;
  await expect(composer).toBeEnabled();
  await composer.fill(text);
  await composer.press('Enter'); // One entry gesture, never a Send fallback.
  await waitForTurn(captured, text, 15000);
  expect(turnPosts(captured)).toHaveLength(before + 1);
  await expect(
    page.getByText(`G2 RV08 acknowledged Task 77 reply [turn ${before + 1}]`, {
      exact: true
    })
  ).toBeVisible();
  await expect(
    page.getByRole('button', {
      name: 'cancel-ai-chat-turn',
      exact: true
    })
  ).toHaveCount(0);
  await expect(composer).toBeEnabled();
}

async function runTaskHint83(page: Page, shared: boolean): Promise<void> {
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
  const originalThread = 'thread-playwright-golden';
  const workOrder: WorkOrder = {
    ...workOrderOverviewPayload,
    priority: 'medium',
    assignee: '',
    assigned_to_username: null,
    tags: [],
    company: '',
    company_contact_name: '',
    company_contact_phone: '',
    job_number: '',
    service_quote: '',
    is_active: true
  };
  const card: BoardCard = {
    id: 77,
    work_order: workOrder.id,
    card_kind: 'subtask',
    status: 'backlog',
    board_order: 0,
    title: TASK_TITLE83,
    description: 'Synthetic card, not its parent',
    assigned_to: null,
    assignee: '',
    scheduled_start: null,
    scheduled_end: null,
    estimated_minutes: null,
    is_active: true,
    created_at: '2026-10-17T00:00:00Z',
    updated_at: '2026-10-17T00:00:00Z',
    work_order_reference: workOrder.reference,
    work_order_title: workOrder.title,
    priority: 'medium',
    lifecycle_status: 'planned',
    lifecycle_version: 1,
    machine: 1,
    machine_name: 'Machine One',
    tags: [],
    effective_assignee: null,
    effective_start: null,
    effective_end: null
  };
  expect(card.id).not.toBe(card.work_order);
  let servedSharedDetail = 0;
  const scopeReads: string[] = [];
  const businessWrites: CapturedRequest[] = [];
  await installG2(page, captured, plan);
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
      await route.abort('blockedbyclient');
      return;
    }
    if (
      !['GET', 'HEAD', 'OPTIONS'].includes(request.method()) &&
      /^\/api\/(kanban|assets)\//.test(url.pathname)
    ) {
      const entry = {
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      };
      captured.push(entry);
      businessWrites.push(entry);
      await route.fulfill({
        status: 403,
        json: { detail: 'business_writes_forbidden_in_fixture' }
      });
      return;
    }
    if (request.method() === 'GET') {
      const collections = new Map<string, unknown>([
        ['/api/kanban/cards/', [card]],
        ['/api/kanban/work-orders/', [workOrder]],
        ['/api/kanban/columns/', []],
        [
          '/api/assets/machines/',
          [
            {
              pk: 1,
              name: 'Machine One',
              description: 'Synthetic machine',
              active: true,
              location: 'Plant A',
              manufacturer: '',
              model: '',
              serial: '',
              barcode_hash: null
            }
          ]
        ]
      ]);
      if (collections.has(url.pathname)) {
        captured.push({
          method: request.method(),
          url: request.url(),
          bodyText: request.postData()
        });
        await route.fulfill({ json: collections.get(url.pathname) });
        return;
      }
    }
    if (
      shared &&
      request.method() === 'GET' &&
      url.pathname === `/api/ai/threads/${originalThread}`
    ) {
      await route.fulfill({
        json: {
          thread_id: originalThread,
          title: 'Server conversation',
          summary: 'Server conversation',
          created_at: '2026-07-15T00:00:00Z',
          updated_at: '2026-07-15T00:01:00Z',
          active_scope: null,
          shared: true,
          messages: []
        }
      });
      servedSharedDetail += 1;
      return;
    }
    const scopeMatch = url.pathname.match(/\/threads\/([^/]+)\/scope$/);
    if (scopeMatch) {
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      });
      if (request.method() !== 'GET') {
        await route.fulfill({
          status: 409,
          json: { detail: 'Task_must_not_change_scope' }
        });
        return;
      }
      const threadId = decodeURIComponent(scopeMatch[1]);
      scopeReads.push(threadId);
      const existing = threadId === originalThread;
      await route.fulfill({
        json: {
          thread_id: threadId,
          scope: {
            schema_version: 1,
            mode: existing ? 'explicit_assets' : 'legacy_unconfirmed',
            machine_ids: existing ? [1] : [],
            date_window: { from: null, to: null },
            source_classes: [
              'controlled_document',
              'asset_attachment',
              'work_order',
              'maintenance_record'
            ],
            display_label: existing ? 'Machine One' : ''
          },
          version: existing ? 4 : 0,
          hash: existing ? 'f'.repeat(64) : '',
          display_label: existing ? 'Machine One' : '',
          editable: true
        }
      });
      return;
    }
    if (
      request.method() === 'PUT' &&
      /\/threads\/[^/]+\/focus$/.test(url.pathname)
    ) {
      const body: {
        focus: { kind: string; id: number };
        expected_focus_version: number;
      } = request.postDataJSON();
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      });
      plan.version += 1;
      plan.current =
        body.focus.kind === 'kanban_card' && body.focus.id === card.id
          ? {
              kind: 'kanban_card',
              id: card.id,
              label: card.title,
              machine_id: 1,
              work_order_id: card.work_order
            }
          : canonicalFocusForG2(body.focus);
      await route.fulfill({
        json: { focus: plan.current, focus_version: plan.version }
      });
      return;
    }
    if (
      request.method() === 'POST' &&
      /\/api\/ai\/chat\/stream\/?$/.test(url.pathname)
    ) {
      captured.push({
        method: request.method(),
        url: request.url(),
        bodyText: request.postData()
      });
      const body: { thread_id: string } = request.postDataJSON();
      const index = turnPosts(captured).length;
      const runId = `run-task83-${index}`;
      const messageId = `message-task83-${index}`;
      const events: SSEEvent[] = [
        { type: 'RUN_STARTED', threadId: body.thread_id, runId },
        {
          type: 'TEXT_MESSAGE_START',
          threadId: body.thread_id,
          runId,
          messageId,
          role: 'assistant'
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          threadId: body.thread_id,
          runId,
          messageId,
          delta: `G2 RV08 acknowledged Task 77 reply [turn ${index}]`
        },
        {
          type: 'TEXT_MESSAGE_END',
          threadId: body.thread_id,
          runId,
          messageId
        },
        { type: 'RUN_FINISHED', threadId: body.thread_id, runId }
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
  await page.goto('/playwright/g2-rv0809.html?route=/maintenance/board/');
  await expect
    .poll(async () =>
      page.evaluate(async (moduleUrl) => {
        const { useThreadFocusState } = await import(moduleUrl);
        const binding = useThreadFocusState.getState().binding;
        return { threadId: binding?.threadId, shared: binding?.shared };
      }, '/src/states/ThreadFocusState.ts')
    )
    .toEqual({ threadId: originalThread, shared });
  if (shared) expect(servedSharedDetail).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.__g2!.accountUserId())).toBe(11);
  await expect(page.getByText(TASK_TITLE83, { exact: true })).toBeVisible();
  await page
    .getByRole('button', { name: 'Edit work order', exact: true })
    .click();
  const modal = page.getByRole('dialog', {
    name: 'Edit work order',
    exact: true
  });
  const title = modal.getByLabel('Title', { exact: false });
  await expect(title).toHaveValue(TASK_TITLE83);
  await title.fill(TASK_DRAFT83);
  const offset = captured.length;
  await modal.getByTestId('ask-about-task').click(); // Real Task Ask, no private action call.
  if (shared) {
    await expect
      .poll(
        () =>
          captured
            .slice(offset)
            .filter(
              (entry) =>
                entry.method === 'GET' &&
                /\/threads\/[^/]+\/focus$/.test(new URL(entry.url).pathname) &&
                new URL(entry.url).pathname !==
                  `/api/ai/threads/${originalThread}/focus`
            ).length
      )
      .toBeGreaterThan(0);
  }
  const pending = await page.evaluate(async (moduleUrl) => {
    const { useAIChatState } = await import(moduleUrl);
    const state = useAIChatState.getState();
    return {
      isOpen: state.isOpen,
      targetHint: state.targetHint,
      routingHint: state.routingHint,
      hintThreadId: state.hintThreadId
    };
  }, '/src/states/AIChatState.tsx');
  expect(pending.isOpen).toBe(true);
  expect(pending.targetHint).toEqual({
    kind: 'kanban_card',
    id: 77,
    label: TASK_TITLE83,
    machineId: 1,
    workOrderId: 42
  });
  expect(pending.routingHint).toBeUndefined(); // Task is focus, never implicit scope widening.
  const binding = await page.evaluate(async (moduleUrl) => {
    const { useThreadFocusState } = await import(moduleUrl);
    const value = useThreadFocusState.getState().binding;
    return { threadId: value?.threadId, shared: value?.shared };
  }, '/src/states/ThreadFocusState.ts');
  expect(binding.shared).toBe(false);
  expect(pending.hintThreadId).toBe(binding.threadId);
  if (shared) expect(binding.threadId).not.toBe(originalThread);
  else expect(binding.threadId).toBe(originalThread);
  // An existing owned thread hydrates its server scope. The genuine new
  // client-minted thread is unobserved: createNewThread clears scope, and
  // sends must OMIT its version instead of inventing an observed zero.
  if (shared)
    expect(scopeReads.filter((id) => id === binding.threadId)).toHaveLength(0);
  else
    await expect
      .poll(() => scopeReads.filter((id) => id === binding.threadId).length)
      .toBeGreaterThan(0);
  expect(focusPuts(captured)).toHaveLength(0);
  expect(scopePuts(captured)).toHaveLength(0);
  expect(turnPosts(captured)).toHaveLength(0);
  await expect(title).toHaveValue(TASK_DRAFT83);
  const first = `Task 77 ${shared ? 'shared' : 'owned'} first: bytes Ω.\nNot parent WorkOrder 42.`;
  const second = `Task 77 ${shared ? 'shared' : 'owned'} second: no reapplication.`;
  await sendTaskTyped83(page, first, captured);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(JSON.parse(focusPuts(captured)[0].bodyText ?? '{}')).toEqual({
    focus: { kind: 'kanban_card', id: 77 },
    expected_focus_version: 0
  });
  expect(scopePuts(captured)).toHaveLength(0);
  expect(captured.indexOf(focusPuts(captured)[0])).toBeLessThan(
    captured.indexOf(turnPosts(captured)[0])
  );
  const consumed = await page.evaluate(async (moduleUrl) => {
    const { useAIChatState } = await import(moduleUrl);
    const state = useAIChatState.getState();
    return {
      targetHint: state.targetHint,
      routingHint: state.routingHint,
      hintThreadId: state.hintThreadId
    };
  }, '/src/states/AIChatState.tsx');
  expect(consumed.targetHint).toBeUndefined();
  expect(consumed.routingHint).toBeUndefined();
  expect(consumed.hintThreadId).toBeNull();
  await sendTaskTyped83(page, second, captured);
  await page.waitForTimeout(1500);
  expect(turnPosts(captured)).toHaveLength(2);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(scopePuts(captured)).toHaveLength(0);
  expect(businessWrites).toHaveLength(0);
  await expect(title).toHaveValue(TASK_DRAFT83);
  expect(plan.current).toEqual({
    kind: 'kanban_card',
    id: 77,
    label: TASK_TITLE83,
    machine_id: 1,
    work_order_id: 42
  });
  for (const [index, text] of [first, second].entries()) {
    const body = JSON.parse(turnPosts(captured)[index].bodyText ?? '{}');
    expect(body.thread_id).toBe(binding.threadId);
    expect(body.message).toBe(text);
    expect(body.expected_focus_version).toBe(1);
    if (shared) expect(body).not.toHaveProperty('expected_scope_version');
    else expect(body.expected_scope_version).toBe(4);
  }
  expect(new URL(focusPuts(captured)[0].url).pathname).toBe(
    `/api/ai/threads/${binding.threadId}/focus`
  );
}

test('RV08 owned Task Ask keeps card PK distinct from parent and never widens scope', async ({
  page
}) => {
  await runTaskHint83(page, false);
});

test('RV08 shared Task Ask keeps card PK distinct from parent and never widens scope', async ({
  page
}) => {
  await runTaskHint83(page, true);
});

// Original RV08 acceptance: real producer, two acknowledgments, then boundary.
async function consumedSnapshot87(page: Page) {
  return await page.evaluate(async (moduleUrl) => {
    const { useAIChatState } = await import(moduleUrl);
    const state = useAIChatState.getState();
    return {
      keys: Object.keys(state),
      targetHint: state.targetHint,
      routingHint: state.routingHint,
      hintThreadId: state.hintThreadId,
      sessionGeneration: state.sessionGeneration
    };
  }, '/src/states/AIChatState.tsx');
}

async function assertConsumed87(page: Page): Promise<void> {
  const state = await consumedSnapshot87(page);
  expect(state.keys).toEqual(
    expect.arrayContaining(['targetHint', 'routingHint', 'hintThreadId'])
  );
  expect(state.targetHint).toBeUndefined();
  expect(state.routingHint).toBeUndefined();
  expect(state.hintThreadId).toBeNull();
}

async function sendProducerEntry87(
  page: Page,
  text: string,
  captured: CapturedRequest[],
  kind: 'Task' | 'WorkOrder'
): Promise<void> {
  if (kind === 'Task') await sendTaskTyped83(page, text, captured);
  else await sendTyped(page, text, captured);
}

async function runAcknowledgedBoundary87(
  page: Page,
  kind: 'Task' | 'WorkOrder',
  boundary: 'thread' | 'account'
): Promise<void> {
  const captured: CapturedRequest[] = [];
  // Independent browser request witness, once per actual native request.
  page.on('request', (request) =>
    captured.push({
      method: request.method(),
      url: request.url(),
      bodyText: request.postData()
    })
  );
  if (kind === 'Task') {
    await runTaskHint83(page, false); // Already qualified real Task Ask + two terminal entries.
  } else {
    const inner: CapturedRequest[] = [];
    const plan: FocusMockPlan = {
      version: 0,
      current: null,
      deny: false,
      conflict: false,
      holdPut: false,
      holdConsumed: false,
      holdRelease: null
    };
    await installG2(page, inner, plan);
    await page.goto('/playwright/g2-rv0809.html?route=/workorder/42/');
    await page.getByTestId('ask-about-work-order').click();
    await sendTyped(
      page,
      'WorkOrder first acknowledged boundary control.',
      captured
    );
    await assertConsumed87(page);
    await sendTyped(
      page,
      'WorkOrder second acknowledged no-reapplication control.',
      captured
    );
  }
  expect(turnPosts(captured)).toHaveLength(2);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(scopePuts(captured)).toHaveLength(0);
  expect(JSON.parse(focusPuts(captured)[0].bodyText ?? '{}').focus).toEqual({
    kind: kind === 'Task' ? 'kanban_card' : 'work_order',
    id: kind === 'Task' ? 77 : 42
  });
  await assertConsumed87(page);
  const originalThread = 'thread-playwright-golden';
  let switchedAccount = false;
  const freshContextReads: string[] = [];
  await page.route('**/api/ai/threads**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const match = url.pathname.match(/\/threads\/([^/]+)\/focus$/);
    if (
      request.method() === 'GET' &&
      match &&
      (switchedAccount || decodeURIComponent(match[1]) !== originalThread)
    ) {
      freshContextReads.push(url.pathname);
      await route.fulfill({ json: { focus: null, focus_version: 0 } });
      return;
    }
    await route.fallback();
  });
  if (boundary === 'thread') {
    await page
      .getByRole('button', { name: 'select-ai-chat-thread', exact: true })
      .click();
    await page
      .getByRole('menuitem', { name: 'new-ai-chat-thread', exact: true })
      .click();
  } else {
    const before = await consumedSnapshot87(page);
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
    expect(actor).toEqual({
      id: 22,
      staff: false,
      superuser: false,
      groups: []
    });
    await expect
      .poll(async () => (await consumedSnapshot87(page)).sessionGeneration)
      .toBeGreaterThan(before.sessionGeneration);
    await page.evaluate(async (moduleUrl) => {
      const { useAIChatState } = await import(moduleUrl);
      useAIChatState.getState().open(); // Actual global open, NO replacement hint.
    }, '/src/states/AIChatState.tsx');
  }
  await expect.poll(() => freshContextReads.length).toBeGreaterThan(0);
  await expect
    .poll(
      async () => (await page.evaluate(() => window.__g2!.focusState())).version
    )
    .toBe(0);
  await assertConsumed87(page);
  const binding = await page.evaluate(async (moduleUrl) => {
    const { useThreadFocusState } = await import(moduleUrl);
    return useThreadFocusState.getState().binding;
  }, '/src/states/ThreadFocusState.ts');
  expect(binding?.shared).toBe(false);
  if (boundary === 'thread') expect(binding?.threadId).not.toBe(originalThread);
  else expect(await page.evaluate(() => window.__g2!.accountUserId())).toBe(22);
  const text = `${kind} fresh ${boundary} entry: exact bytes Ω, never reuse the consumed target.`;
  await sendProducerEntry87(page, text, captured, kind);
  expect(turnPosts(captured)).toHaveLength(3);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(scopePuts(captured)).toHaveLength(0);
  const body = JSON.parse(turnPosts(captured)[2].bodyText ?? '{}');
  expect(body.thread_id).toBe(binding?.threadId);
  expect(body.message).toBe(text);
  expect(body.expected_focus_version).toBe(0);
  await assertConsumed87(page);
  if (boundary === 'thread') {
    // Exercise a real persisted-thread selection as well as creation. The
    // old server focus can be re-read, but its old hint must NOT be applied.
    await page
      .getByRole('button', { name: 'select-ai-chat-thread', exact: true })
      .click();
    await page.getByRole('menuitem', { name: /Server conversation/ }).click();
    await expect
      .poll(async () =>
        page.evaluate(async (moduleUrl) => {
          const { useThreadFocusState } = await import(moduleUrl);
          return useThreadFocusState.getState().binding?.threadId;
        }, '/src/states/ThreadFocusState.ts')
      )
      .toBe(originalThread);
    await assertConsumed87(page);
    const back = `${kind} return to original persisted thread without hint reapplication.`;
    await sendProducerEntry87(page, back, captured, kind);
    expect(turnPosts(captured)).toHaveLength(4);
    expect(focusPuts(captured)).toHaveLength(1);
    expect(scopePuts(captured)).toHaveLength(0);
    const returned = JSON.parse(turnPosts(captured)[3].bodyText ?? '{}');
    expect(returned.thread_id).toBe(originalThread);
    expect(returned.message).toBe(back);
  }
  await page.waitForTimeout(1000);
  expect(focusPuts(captured)).toHaveLength(1);
  expect(scopePuts(captured)).toHaveLength(0);
  expect(
    captured.filter(
      (entry) =>
        !['GET', 'HEAD', 'OPTIONS'].includes(entry.method) &&
        /^\/api\/(kanban|assets)\//.test(new URL(entry.url).pathname)
    )
  ).toHaveLength(0);
}

for (const kind of ['WorkOrder', 'Task'] as const) {
  test(`RV08 acknowledged ${kind} hint stays consumed across native thread changes`, async ({
    page
  }) => {
    await runAcknowledgedBoundary87(page, kind, 'thread');
  });
  test(`RV08 acknowledged ${kind} hint stays consumed across account-store changes`, async ({
    page
  }) => {
    await runAcknowledgedBoundary87(page, kind, 'account');
  });
}
