/**
 * Shared mocked-chat harness for the AI-chat Playwright suites (S11 WP-C1).
 *
 * Extracted verbatim from pui_ai_chat.spec.ts so the evidence suite
 * (pui_ai_chat_evidence.spec.ts) exercises the SAME foundation: one scope
 * mock, one thread mock, one request observer. New capabilities are
 * additive options — the original spec's behavior is byte-identical with
 * no options passed.
 */

import type { Page, Request, Route } from '@playwright/test';

import { expect } from '../baseFixtures.js';

export const threadId = 'thread-playwright-golden';

export type SSEEvent = Record<string, unknown>;

export interface ObservedRequest {
  url: string;
  method: string;
  headers: Record<string, string>;
  body?: Record<string, unknown>;
}

export interface MockScopeState {
  mode: string;
  version: number;
  machineIds: number[];
  displayLabel: string;
}

export interface FoundationObservations {
  threadReads: ObservedRequest[];
  threadMutations: ObservedRequest[];
  /** S1: GET/PUT /threads/{id}/scope traffic. */
  scopeReads: ObservedRequest[];
  scopeMutations: ObservedRequest[];
  /** S11: GET /threads/{id}/evidence-sets/... traffic. */
  evidenceSetReads: ObservedRequest[];
  /** S1: mutable server-side scope the mock serves; tests may pre-seed it. */
  scope: MockScopeState;
}

export interface FoundationOptions {
  /** Override the `messages` array in the thread-detail GET response (reload fidelity). */
  threadDetailMessages?: unknown[];
  /** Evidence-set member pages by set id; unknown ids 404 (S11). */
  evidenceSets?: Record<
    string,
    {
      members: unknown[];
      population_count: number;
      complete: boolean;
      next_cursor?: string | null;
    }
  >;
}

export const goldenEvents: SSEEvent[] = [
  {
    type: 'RUN_STARTED',
    threadId,
    runId: 'run-golden'
  },
  {
    type: 'WORKFLOW_STARTED',
    threadId,
    runId: 'run-golden',
    workflow_id: 'wf1',
    workflow_name: 'T6_DIAGNOSTICS'
  },
  {
    type: 'TEXT_MESSAGE_START',
    threadId,
    runId: 'run-golden',
    messageId: 'message-golden',
    role: 'assistant'
  },
  {
    type: 'TEXT_MESSAGE_CONTENT',
    threadId,
    runId: 'run-golden',
    messageId: 'message-golden',
    delta: 'Golden typed response'
  },
  {
    type: 'TEXT_MESSAGE_END',
    threadId,
    runId: 'run-golden',
    messageId: 'message-golden'
  },
  {
    type: 'RUN_FINISHED',
    threadId,
    runId: 'run-golden'
  }
];

export function sseBody(events: SSEEvent[] = goldenEvents): string {
  return `${events
    .map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`)
    .join('')}data: [DONE]\n\n`;
}

export function aguiBody(events: SSEEvent[]): string {
  return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('');
}

export async function observeRequest(
  request: Request
): Promise<ObservedRequest> {
  let body: Record<string, unknown> | undefined;
  if (request.postData()) {
    try {
      body = request.postDataJSON() as Record<string, unknown>;
    } catch {
      // Multipart uploads are asserted from their raw body separately.
    }
  }

  return {
    url: request.url(),
    method: request.method(),
    headers: await request.allHeaders(),
    body
  };
}

export async function mockChatFoundation(
  page: Page,
  options: FoundationOptions = {}
): Promise<FoundationObservations> {
  const observations: FoundationObservations = {
    threadReads: [],
    threadMutations: [],
    scopeReads: [],
    scopeMutations: [],
    evidenceSetReads: [],
    scope: {
      mode: 'legacy_unconfirmed',
      version: 0,
      machineIds: [],
      displayLabel: ''
    }
  };

  const scopeSummary = () => ({
    mode: observations.scope.mode,
    version: observations.scope.version,
    display_label: observations.scope.displayLabel
  });

  const scopePayload = () => ({
    thread_id: threadId,
    scope: {
      schema_version: 1,
      mode: observations.scope.mode,
      machine_ids: observations.scope.machineIds,
      date_window: { from: null, to: null },
      source_classes: [
        'controlled_document',
        'asset_attachment',
        'work_order',
        'maintenance_record'
      ],
      display_label: observations.scope.displayLabel
    },
    version: observations.scope.version,
    hash: observations.scope.version > 0 ? 'f'.repeat(64) : '',
    display_label: observations.scope.displayLabel,
    editable: true
  });

  await page.route('**/api/approvals/count/**', async (route: Route) => {
    await route.fulfill({ json: { count: 0 } });
  });

  await page.route('**/api/ai/threads**', async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const isThreadDetail = url.pathname.endsWith(`/threads/${threadId}`);

    // S1: the scope endpoints share the /threads glob — this branch must
    // come FIRST or a scope GET would fall into the detail fallthrough.
    if (/\/threads\/[^/]+\/scope$/.test(url.pathname)) {
      if (request.method() === 'PUT') {
        const observed = await observeRequest(request);
        observations.scopeMutations.push(observed);
        const requested = (observed.body?.scope ?? {}) as Record<
          string,
          unknown
        >;
        observations.scope.mode = String(
          requested.mode ?? observations.scope.mode
        );
        observations.scope.machineIds = Array.isArray(requested.machine_ids)
          ? (requested.machine_ids as number[])
          : [];
        observations.scope.displayLabel = String(requested.display_label ?? '');
        observations.scope.version += 1;
        await route.fulfill({ json: scopePayload() });
        return;
      }
      observations.scopeReads.push(await observeRequest(request));
      await route.fulfill({ json: scopePayload() });
      return;
    }

    // S11: evidence-set expansion shares the glob too — before the detail
    // fallthrough (the same ordering trap as the scope branch above).
    const evidenceMatch = url.pathname.match(
      /\/threads\/[^/]+\/evidence-sets\/([^/]+)\/members$/
    );
    if (evidenceMatch) {
      observations.evidenceSetReads.push(await observeRequest(request));
      const setPage = options.evidenceSets?.[evidenceMatch[1]];
      if (!setPage) {
        await route.fulfill({ status: 404, json: { detail: 'Not found' } });
        return;
      }
      await route.fulfill({
        json: { next_cursor: null, ...setPage }
      });
      return;
    }

    if (isThreadDetail && request.method() === 'PUT') {
      observations.threadMutations.push(await observeRequest(request));
      await route.fulfill({
        json: {
          thread_id: threadId,
          title: url.searchParams.get('title'),
          updated: true
        }
      });
      return;
    }

    if (isThreadDetail && request.method() === 'DELETE') {
      observations.threadMutations.push(await observeRequest(request));
      await route.fulfill({
        json: { status: 'deleted', thread_id: threadId }
      });
      return;
    }

    observations.threadReads.push(await observeRequest(request));

    if (isThreadDetail) {
      await route.fulfill({
        json: {
          thread_id: threadId,
          title: 'Server conversation',
          summary: 'Server conversation',
          created_at: '2026-07-15T00:00:00Z',
          updated_at: '2026-07-15T00:01:00Z',
          active_scope: scopeSummary(),
          messages: options.threadDetailMessages ?? [
            {
              id: 'server-message',
              role: 'assistant',
              content: 'Durable server history',
              timestamp: '2026-07-15T00:01:00Z'
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
            title: 'Server conversation',
            message_count: 1,
            turn_count: 1,
            summary: 'Server conversation',
            created_at: '2026-07-15T00:00:00Z',
            last_activity: '2026-07-15T00:01:00Z',
            is_persisted: true,
            active_scope: scopeSummary()
          }
        ],
        sync_token: null,
        has_more: false,
        capabilities: { thread_scope: true }
      }
    });
  });

  return observations;
}

export async function seedLegacyHistory(page: Page) {
  await page.evaluate(
    ({ durableThreadId }) => {
      localStorage.setItem('ai-chat-drawer-active-tab', JSON.stringify('chat'));
      localStorage.setItem(
        'ai-chat-threads',
        JSON.stringify([
          {
            id: durableThreadId,
            title: 'Stale local server title',
            messages: [
              {
                id: 'stale-message',
                role: 'assistant',
                content: 'Stale persisted local history',
                timestamp: '2026-07-14T00:00:00Z'
              }
            ],
            createdAt: '2026-07-14T00:00:00Z',
            updatedAt: '2026-07-14T00:01:00Z',
            isPersisted: true
          },
          {
            id: 'legacy-local-only',
            title: 'Legacy local conversation',
            messages: [
              {
                id: 'legacy-message',
                role: 'user',
                content: 'Local-only compatible history',
                timestamp: '2026-07-13T00:00:00Z'
              }
            ],
            createdAt: '2026-07-13T00:00:00Z',
            updatedAt: '2026-07-13T00:01:00Z'
          }
        ])
      );
    },
    { durableThreadId: threadId }
  );
}

export async function openChat(page: Page) {
  await page.getByLabel('open-ai-chat').click();
  // 'AI Assistant' also appears in the nav button and menu entries, so the
  // drawer-open assertion must not use a strict single-element match.
  await expect(
    page.getByText('AI Assistant', { exact: true }).first()
  ).toBeVisible();
}

export function expectCredentialedUnsafeRequest(request: ObservedRequest) {
  expect(request.headers.cookie).toBeTruthy();
  expect(request.headers['x-csrftoken']).toBeTruthy();
  expect(request.headers['x-user-id']).toBeUndefined();
}

export function expectNoCallerAuthority(request: ObservedRequest) {
  expect(request.headers['x-user-id']).toBeUndefined();
  expect(request.body).not.toHaveProperty('user_id');
  expect(request.body).not.toHaveProperty('context');
}

export function requiredRequest(
  request: ObservedRequest | null,
  description: string
): ObservedRequest {
  if (!request) {
    throw new Error(`${description} was not observed`);
  }
  return request;
}
