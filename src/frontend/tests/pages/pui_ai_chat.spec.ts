import { expect, test } from '../baseFixtures.js';
import { doCachedLogin } from '../login.js';
// S11 (WP-C1): the shared mocked-chat foundation lives in aichat_harness.ts
// so the evidence suite exercises the SAME mocks; behavior here is
// byte-identical to the pre-extraction inline definitions.
import {
  type ObservedRequest,
  aguiBody,
  expectCredentialedUnsafeRequest,
  expectNoCallerAuthority,
  goldenEvents,
  mockChatFoundation,
  observeRequest,
  openChat,
  requiredRequest,
  seedLegacyHistory,
  sseBody,
  threadId
} from './aichat_harness.js';

test('typed chat uses authoritative server history and renders ordered AG-UI events', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  const foundation = await mockChatFoundation(page);
  await seedLegacyHistory(page);

  let streamRequest: ObservedRequest | null = null;
  await page.route('**/api/ai/chat/stream', async (route) => {
    streamRequest = await observeRequest(route.request());
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });

  await page.reload();
  await openChat(page);

  await expect(
    page.getByText('Server conversation', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Durable server history', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Stale persisted local history', { exact: true })
  ).toHaveCount(0);

  await page.getByLabel('select-ai-chat-thread').click();
  await expect(
    page.getByText('Legacy local conversation', { exact: true })
  ).toBeVisible();
  await page.getByLabel('select-ai-chat-thread').click();

  await page.getByPlaceholder('Type a message...').fill('Inspect the pump');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toHaveCount(1);

  expect(goldenEvents.map((event) => event.type)).toEqual([
    'RUN_STARTED',
    'WORKFLOW_STARTED',
    'TEXT_MESSAGE_START',
    'TEXT_MESSAGE_CONTENT',
    'TEXT_MESSAGE_END',
    'RUN_FINISHED'
  ]);
  const sentTurn = requiredRequest(streamRequest, 'stream request');
  expectCredentialedUnsafeRequest(sentTurn);
  expectNoCallerAuthority(sentTurn);
  expect(sentTurn.body?.message).toBe('Inspect the pump');
  expect(sentTurn.body?.thread_id).toBe(threadId);
  expect(sentTurn.body?.idempotency_key).toBeTruthy();
  expect(sentTurn.headers['idempotency-key']).toBe(
    sentTurn.body?.idempotency_key
  );

  // The turn must target the same authoritative backend the app uses for
  // its credentialed thread reads. In dev the frontend and Django run on
  // different ports, so window.location.origin is NOT the backend origin;
  // when INVENTREE_SETTINGS.api_host is injected it must match exactly.
  const injectedApiHost = await page.evaluate(
    () => (window as any).INVENTREE_SETTINGS?.api_host || null
  );
  const turnOrigin = new URL(sentTurn.url).origin;
  if (injectedApiHost) {
    expect(turnOrigin).toBe(new URL(injectedApiHost).origin);
  }
  const readOrigins = foundation.threadReads.map(
    (request) => new URL(request.url).origin
  );
  expect(readOrigins).toContain(turnOrigin);

  const listRequest = foundation.threadReads.find((request) =>
    new URL(request.url).pathname.endsWith('/threads')
  );
  expect(listRequest).toBeDefined();
  expect(new URL(listRequest!.url).searchParams.has('user_id')).toBe(false);
  expect(listRequest?.headers['x-user-id']).toBeUndefined();

  const stored = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('ai-chat-threads') || '[]')
  );
  const durable = stored.find((thread: any) => thread.id === threadId);
  expect(
    durable.messages.some(
      (message: any) => message.content === 'Durable server history'
    )
  ).toBe(true);
  expect(stored.some((thread: any) => thread.id === 'legacy-local-only')).toBe(
    true
  );
});

test('tool activity strip shows the completed duration', async ({
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
        {
          type: 'TOOL_CALL_START',
          threadId,
          runId: 'run-golden',
          toolCallId: 'tool-search-work-orders',
          toolCallName: 'search_work_orders'
        },
        {
          type: 'TOOL_CALL_END',
          threadId,
          runId: 'run-golden',
          toolCallId: 'tool-search-work-orders',
          toolCallName: 'search_work_orders',
          status: 'ok',
          durationMs: 428.7
        },
        ...goldenEvents.slice(2)
      ])
    });
  });

  await page.reload();
  await openChat(page);
  await page.getByPlaceholder('Type a message...').fill('List work orders');
  await page.getByLabel('send-ai-chat-message').click();

  const activity = page.getByText('search_work_orders (429 ms)', {
    exact: true
  });
  await expect(activity).toBeVisible();
  await activity.hover();
  await expect(
    page.getByText('search_work_orders completed in 429 ms', { exact: true })
  ).toBeVisible();
});

test('persisted structured question renders its prompt exactly once', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);
  await seedLegacyHistory(page);

  await page.route('**/api/ai/threads**', async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith(`/threads/${threadId}`)) {
      await route.fulfill({
        json: {
          thread_id: threadId,
          title: 'Question conversation',
          summary: 'Question conversation',
          created_at: '2026-07-15T00:00:00Z',
          updated_at: '2026-07-15T00:01:00Z',
          messages: [
            {
              id: 'question-message',
              role: 'assistant',
              content: 'Which machine do you mean?\n\n1. Pump A\n2. Pump B',
              timestamp: '2026-07-15T00:01:00Z',
              question: {
                kind: 'clarification_question',
                interrupt_id: 'interrupt-question-once',
                question_text: 'Which machine do you mean?',
                options: [
                  { id: 'machine:1', kind: 'machine', label: 'Pump A' },
                  { id: 'machine:2', kind: 'machine', label: 'Pump B' }
                ],
                expires_at: '2099-01-01T00:00:00Z',
                source: 'manual_search_ambiguity'
              }
            }
          ]
        }
      });
      return;
    }
    await route.fulfill({
      json: {
        threads: [
          {
            thread_id: threadId,
            title: 'Question conversation',
            message_count: 1,
            turn_count: 1,
            summary: 'Question conversation',
            created_at: '2026-07-15T00:00:00Z',
            last_activity: '2026-07-15T00:01:00Z',
            is_persisted: true
          }
        ],
        sync_token: null,
        has_more: false
      }
    });
  });

  await page.reload();
  await openChat(page);

  await expect(
    page.getByText('Which machine do you mean?', { exact: true })
  ).toHaveCount(1);
  await expect(page.getByText('Pump A', { exact: true })).toBeVisible();
  await expect(page.getByText('Pump B', { exact: true })).toBeVisible();
});

test('auto wire waits for capability sync before its first send', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await seedLegacyHistory(page);

  await page.route('**/api/approvals/count/**', async (route) => {
    await route.fulfill({ json: { count: 0 } });
  });

  let releaseThreadSync: (() => void) | undefined;
  const heldThreadSync = new Promise<void>((resolve) => {
    releaseThreadSync = resolve;
  });
  await page.route('**/api/ai/threads**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith(`/threads/${threadId}`)) {
      if (request.method() === 'PUT') {
        await route.fulfill({
          json: { thread_id: threadId, title: 'Server conversation' }
        });
        return;
      }
      await route.fulfill({
        json: {
          thread_id: threadId,
          title: 'Server conversation',
          summary: 'Server conversation',
          created_at: '2026-07-15T00:00:00Z',
          updated_at: '2026-07-15T00:01:00Z',
          messages: []
        }
      });
      return;
    }

    await heldThreadSync;
    await route.fulfill({
      json: {
        threads: [
          {
            thread_id: threadId,
            title: 'Server conversation',
            message_count: 0,
            turn_count: 0,
            summary: 'Server conversation',
            created_at: '2026-07-15T00:00:00Z',
            last_activity: '2026-07-15T00:01:00Z',
            is_persisted: true
          }
        ],
        sync_token: null,
        has_more: false,
        capabilities: { agui: true }
      }
    });
  });

  let aguiRequests = 0;
  let legacyRequests = 0;
  await page.route('**/api/ai/agui', async (route) => {
    aguiRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: aguiBody([goldenEvents[0], ...goldenEvents.slice(2)])
    });
  });
  await page.route('**/api/ai/chat/stream', async (route) => {
    legacyRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });

  await page.evaluate(() => localStorage.removeItem('aimms.wire'));
  await page.reload();
  await openChat(page);

  const composer = page.getByPlaceholder('Type a message...');
  await expect(composer).toBeDisabled();
  releaseThreadSync?.();
  await expect(composer).toBeEnabled();

  await composer.fill('Use the advertised wire');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();
  expect(aguiRequests).toBe(1);
  expect(legacyRequests).toBe(0);
});

test('proposal refresh event renders a newly available card within two seconds', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  let proposalReads = 0;
  let proposalAvailable = false;
  await page.route('**/api/aichat/proposals/**', async (route) => {
    if (route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    proposalReads += 1;
    await route.fulfill({
      json: {
        results: !proposalAvailable
          ? []
          : [
              {
                id: 'proposal-phase-9-refresh',
                action_type: 'work_order.hold',
                state: 'proposed',
                work_order_id: 130,
                target_version: 1,
                preview: {
                  reference: 'WO-000130',
                  title: 'Phase 9 immediate proposal',
                  current_status: 'in_progress',
                  resulting_status: 'on_hold'
                },
                reason: 'Awaiting a replacement belt',
                expires_at: '2099-01-01T00:00:00Z',
                receipt: null,
                failure_code: null
              }
            ]
      }
    });
  });

  await page.reload();
  await openChat(page);
  await page.getByRole('tab', { name: 'Approvals', exact: true }).click();
  await expect.poll(() => proposalReads).toBeGreaterThanOrEqual(1);

  const readsBeforeRefresh = proposalReads;
  proposalAvailable = true;
  const started = Date.now();
  await page.evaluate(() =>
    window.dispatchEvent(new CustomEvent('aimms:proposals-refresh'))
  );
  await expect(
    page.getByText('Phase 9 immediate proposal', { exact: true })
  ).toBeVisible({ timeout: 2_000 });
  expect(Date.now() - started).toBeLessThanOrEqual(2_000);
  expect(proposalReads).toBeGreaterThan(readsBeforeRefresh);
});

test('AG-UI endpoint removal falls back once and latches legacy', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  let aguiRequests = 0;
  let legacyRequests = 0;
  await page.route('**/api/ai/agui', async (route) => {
    aguiRequests += 1;
    await route.fulfill({ status: 404, body: 'Not Found' });
  });
  await page.route('**/api/ai/chat/stream', async (route) => {
    legacyRequests += 1;
    const responseEvents = goldenEvents.map((event) =>
      event.type === 'TEXT_MESSAGE_CONTENT'
        ? { ...event, delta: `Legacy response ${legacyRequests}` }
        : event
    );
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody(responseEvents)
    });
  });

  await page.evaluate(() => localStorage.setItem('aimms.wire', 'agui'));
  await page.reload();
  await openChat(page);
  const composer = page.getByPlaceholder('Type a message...');
  await expect(composer).toBeEnabled();

  await composer.fill('First turn after flag removal');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Legacy response 1', { exact: true })
  ).toBeVisible();

  await composer.fill('Second turn after flag removal');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Legacy response 2', { exact: true })
  ).toBeVisible();

  expect(aguiRequests).toBe(1);
  expect(legacyRequests).toBe(2);
  await page.evaluate(() => localStorage.removeItem('aimms.wire'));
});

test('typed chat reuses its idempotency key and removes partial output on retry', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  const attempts: ObservedRequest[] = [];
  await page.route('**/api/ai/chat/stream', async (route) => {
    attempts.push(await observeRequest(route.request()));
    if (attempts.length === 1) {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody([
          goldenEvents[0],
          goldenEvents[2],
          {
            ...goldenEvents[3],
            delta: 'Partial response must be replaced'
          },
          {
            type: 'RUN_ERROR',
            threadId,
            runId: 'run-golden',
            message: 'Connection timeout'
          }
        ])
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });

  await page.reload();
  await openChat(page);
  await page.getByPlaceholder('Type a message...').fill('Retry this turn');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible({ timeout: 10_000 });
  await expect(
    page.getByText('Partial response must be replaced', { exact: true })
  ).toHaveCount(0);
  expect(attempts).toHaveLength(2);

  const keys = attempts.map((attempt) => attempt.body?.idempotency_key);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  for (const attempt of attempts) {
    expect(attempt.headers['idempotency-key']).toBe(keys[0]);
    expectCredentialedUnsafeRequest(attempt);
    expectNoCallerAuthority(attempt);
  }
});

test('typed chat cancellation is visible and durably retained locally', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  let markStarted: (() => void) | undefined;
  let releaseRoute: (() => void) | undefined;
  const started = new Promise<void>((resolve) => {
    markStarted = resolve;
  });
  const heldRoute = new Promise<void>((resolve) => {
    releaseRoute = resolve;
  });

  await page.route('**/api/ai/chat/stream', async (route) => {
    markStarted?.();
    await heldRoute;
    try {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sseBody()
      });
    } catch {
      // The browser has already canceled this held request.
    }
  });

  await page.reload();
  await openChat(page);
  await page.getByPlaceholder('Type a message...').fill('Cancel this turn');
  await page.getByLabel('send-ai-chat-message').click();
  await started;

  await page.getByLabel('cancel-ai-chat-turn').click();
  await expect(
    page.getByText('(Message cancelled)', { exact: true })
  ).toBeVisible();

  const storedMessages = await page.evaluate(
    ({ durableThreadId }) => {
      const threads = JSON.parse(
        localStorage.getItem('ai-chat-threads') || '[]'
      );
      return threads.find((thread: any) => thread.id === durableThreadId)
        ?.messages;
    },
    { durableThreadId: threadId }
  );
  expect(
    storedMessages.some(
      (message: any) => message.content === '(Message cancelled)'
    )
  ).toBe(true);

  releaseRoute?.();
});

test('upload metadata is credentialed and carried into the following typed turn', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  let uploadRequest: ObservedRequest | null = null;
  let uploadBody = '';
  let chatRequest: ObservedRequest | null = null;

  await page.route('**/api/ai/upload', async (route) => {
    uploadRequest = await observeRequest(route.request());
    uploadBody = route.request().postData() || '';
    await route.fulfill({
      json: {
        file_id: `${threadId}/manual.pdf`,
        filename: 'manual.pdf',
        size: 7,
        content_type: 'application/pdf',
        thread_id: threadId
      }
    });
  });
  await page.route('**/api/ai/chat/stream', async (route) => {
    chatRequest = await observeRequest(route.request());
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });

  await page.reload();
  await openChat(page);
  await page.locator('input[type="file"]').setInputFiles({
    name: 'manual.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('manual')
  });
  await expect(page.getByText('manual.pdf', { exact: true })).toBeVisible();
  await expect(page.getByLabel('send-ai-chat-message')).toBeDisabled();

  await page
    .getByPlaceholder('Add a message about attached files...')
    .fill('Use the manual');
  await page.getByLabel('send-ai-chat-message').click();
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();

  const sentUpload = requiredRequest(uploadRequest, 'upload request');
  const sentChat = requiredRequest(chatRequest, 'chat request');
  expectCredentialedUnsafeRequest(sentUpload);
  expect(sentUpload.headers['x-user-id']).toBeUndefined();
  expect(uploadBody).toContain('name="thread_id"');
  expect(uploadBody).toContain(threadId);
  expect(uploadBody).toContain('filename="manual.pdf"');

  expect(sentChat.body?.file_ids).toEqual([`${threadId}/manual.pdf`]);
  expectNoCallerAuthority(sentChat);
  expect(sentChat.headers['idempotency-key']).toBe(
    sentChat.body?.idempotency_key
  );

  // A new thread cannot inherit an attachment uploaded for this one.
  await page.locator('input[type="file"]').setInputFiles({
    name: 'manual.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('manual')
  });
  await expect(page.getByText('manual.pdf', { exact: true })).toBeVisible();
  await page.getByLabel('select-ai-chat-thread').click();
  await page.getByRole('menuitem', { name: 'new-ai-chat-thread' }).click();
  await expect(page.getByText('manual.pdf', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('send-ai-chat-message')).toBeDisabled();
});

test('thread rename and delete use credentialed server mutations', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  const foundation = await mockChatFoundation(page);

  await page.reload();
  await openChat(page);
  await page.getByLabel('select-ai-chat-thread').click();

  page.once('dialog', async (dialog) => {
    expect(dialog.type()).toBe('prompt');
    await dialog.accept('Renamed server conversation');
  });
  await page.getByLabel(`rename-ai-chat-thread-${threadId}`).click();
  // The renamed title legitimately renders in both the thread selector and
  // the thread menu, so a strict single-element match would be wrong here.
  await expect(
    page.getByText('Renamed server conversation', { exact: true }).first()
  ).toBeVisible();
  await expect.poll(() => foundation.threadMutations.length).toBe(1);

  const deleteButton = page.getByLabel(`delete-ai-chat-thread-${threadId}`);
  if (!(await deleteButton.isVisible())) {
    await page.getByLabel('select-ai-chat-thread').click();
  }
  await deleteButton.click();
  await expect.poll(() => foundation.threadMutations.length).toBe(2);

  for (const mutation of foundation.threadMutations) {
    expectCredentialedUnsafeRequest(mutation);
    expect(new URL(mutation.url).searchParams.has('user_id')).toBe(false);
  }
  expect(foundation.threadMutations.map((request) => request.method)).toEqual([
    'PUT',
    'DELETE'
  ]);
});

/**
 * S2: the scope banner renders the unconfirmed state for threads without a
 * typed scope, and a new thread never inherits one.
 */
test('scope banner shows unconfirmed and resets on a new thread', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  await page.reload();
  await openChat(page);

  await expect(page.getByTestId('ai-chat-scope-banner')).toBeVisible();
  await expect(page.getByTestId('ai-chat-scope-label')).toContainText(
    'Scope unconfirmed'
  );

  await page.getByLabel('new-ai-chat-thread').click();
  await expect(page.getByTestId('ai-chat-scope-label')).toContainText(
    'Scope unconfirmed'
  );
});

/**
 * S2: a machine launch sets the scope server-side BEFORE the first send —
 * the typed text goes out byte-identical (no `[Machine: ...]` prefix) and
 * carries the fresh scope version.
 */
test('machine hint becomes a server-side scope, never message text', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  const foundation = await mockChatFoundation(page);

  let streamRequest: ObservedRequest | null = null;
  await page.route('**/api/ai/chat/stream', async (route) => {
    streamRequest = await observeRequest(route.request());
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });
  await page.route(/\/api\/barcode\//, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: 'Match found',
        barcode: 'INV-AM1',
        assetmachine: {
          pk: 1,
          api_url: '/api/assets/machines/1/',
          web_url: '/web/machines/machine/1/',
          instance: { pk: 1, name: 'Scan Target Machine' }
        }
      })
    })
  );

  await page.getByLabel('barcode-scan-button-any').click();
  await page.getByLabel('barcode-scan-keyboard-input').fill('INV-AM1');
  await page.keyboard.press('Enter');
  await page.getByTestId('ai-chat-routing-hint').waitFor();

  const composer = page.getByPlaceholder(/Type a message|Answer the question/);
  await composer.fill('What is wrong?');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();

  // Exactly one scope PUT, before the turn (proven by the version echo).
  expect(foundation.scopeMutations).toHaveLength(1);
  const scopePut = foundation.scopeMutations[0];
  expectCredentialedUnsafeRequest(scopePut);
  expect(scopePut.body?.expected_version).toBe(0);
  expect(scopePut.body?.scope).toMatchObject({
    mode: 'explicit_assets',
    machine_ids: [1],
    display_label: 'Scan Target Machine'
  });

  const sentTurn = requiredRequest(streamRequest, 'stream request');
  expect(sentTurn.body?.message).toBe('What is wrong?');
  expect(String(sentTurn.body?.message)).not.toContain('[Machine:');
  expect(sentTurn.body?.expected_scope_version).toBe(1);

  await expect(page.getByTestId('ai-chat-scope-label')).toContainText(
    'Scan Target Machine'
  );
  await page.unroute(/\/api\/barcode\//);
});

/**
 * S1/S2: a send bounced by 409 scope_version_conflict refreshes the scope
 * and offers a one-click resend that carries the refreshed version.
 */
test('scope version conflict refreshes and resends', async ({ browser }) => {
  const page = await doCachedLogin(browser);
  const foundation = await mockChatFoundation(page);
  foundation.scope.mode = 'all_authorized_assets';
  foundation.scope.version = 2;
  foundation.scope.displayLabel = 'Authorized fleet';

  const streamBodies: ObservedRequest[] = [];
  let bounced = false;
  await page.route('**/api/ai/chat/stream', async (route) => {
    streamBodies.push(await observeRequest(route.request()));
    if (!bounced) {
      bounced = true;
      await route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'scope_version_conflict' })
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });

  await page.reload();
  await openChat(page);
  await expect(page.getByTestId('ai-chat-scope-label')).toContainText(
    'Authorized fleet'
  );

  const composer = page.getByPlaceholder(/Type a message|Answer the question/);
  await composer.fill('Check the inverter');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(
    page.getByText(/The conversation scope changed/).first()
  ).toBeVisible();
  const resend = page.getByTestId('ai-chat-scope-resend');
  await expect(resend).toBeVisible();
  // The conflict handler re-fetched the authoritative scope.
  await expect.poll(() => foundation.scopeReads.length).toBeGreaterThan(0);

  await resend.click();
  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();

  expect(streamBodies).toHaveLength(2);
  expect(streamBodies[1].body?.expected_scope_version).toBe(2);
  expect(streamBodies[1].body?.message).toBe('Check the inverter');
});

/**
 * S2: a reload restores the scope from the server, never localStorage.
 */
test('scope survives reload from the server', async ({ browser }) => {
  const page = await doCachedLogin(browser);
  const foundation = await mockChatFoundation(page);
  foundation.scope.mode = 'explicit_assets';
  foundation.scope.version = 3;
  foundation.scope.machineIds = [7];
  foundation.scope.displayLabel = 'Pump 7';

  let streamRequest: ObservedRequest | null = null;
  await page.route('**/api/ai/chat/stream', async (route) => {
    streamRequest = await observeRequest(route.request());
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });

  await page.reload();
  await openChat(page);

  await expect(page.getByTestId('ai-chat-scope-label')).toContainText('Pump 7');

  const composer = page.getByPlaceholder(/Type a message|Answer the question/);
  await composer.fill('Show recent work');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible();
  const sentTurn = requiredRequest(streamRequest, 'stream request');
  expect(sentTurn.body?.expected_scope_version).toBe(3);
});

test('a spent daily token budget is terminal: one request, reset-time copy', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  let streamCalls = 0;
  await page.route('**/api/ai/chat/stream', async (route) => {
    streamCalls++;
    await route.fulfill({
      status: 429,
      contentType: 'application/json',
      body: JSON.stringify({
        error: 'token_budget_exhausted',
        code: 'token_budget_exhausted',
        retry_after: 3600
      })
    });
  });

  await page.reload();
  await openChat(page);
  await page.getByPlaceholder('Type a message...').fill('Count the pumps');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(page.getByText(/daily AI usage limit/i).first()).toBeVisible();
  await expect(page.getByText(/resets at/i).first()).toBeVisible();
  // S12: never auto-retried — the reset is hours away, not seconds.
  expect(streamCalls).toBe(1);
});

test('a rate-window 429 retries bounded and then reports rate copy', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  let streamCalls = 0;
  await page.route('**/api/ai/chat/stream', async (route) => {
    streamCalls++;
    await route.fulfill({
      status: 429,
      contentType: 'application/json',
      body: JSON.stringify({
        error: 'rate_limit_exceeded',
        code: 'rate_limited',
        retry_after: 1
      })
    });
  });

  await page.reload();
  await openChat(page);
  await page.getByPlaceholder('Type a message...').fill('Count the pumps');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(page.getByText(/too many requests/i).first()).toBeVisible({
    timeout: 15000
  });
  // Bounded retry: exactly maxAttempts requests, no more.
  expect(streamCalls).toBe(3);
});

test('a capacity 503 retries after the server Retry-After and succeeds', async ({
  browser
}) => {
  const page = await doCachedLogin(browser);
  await mockChatFoundation(page);

  let streamCalls = 0;
  await page.route('**/api/ai/chat/stream', async (route) => {
    streamCalls++;
    if (streamCalls === 1) {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({
          error: 'ai_capacity_busy',
          code: 'ai_capacity_busy',
          retry_after: 1
        })
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: sseBody()
    });
  });

  await page.reload();
  await openChat(page);
  await page.getByPlaceholder('Type a message...').fill('Count the pumps');
  await page.getByLabel('send-ai-chat-message').click();

  await expect(
    page.getByText('Golden typed response', { exact: true })
  ).toBeVisible({ timeout: 15000 });
  expect(streamCalls).toBe(2);
});
