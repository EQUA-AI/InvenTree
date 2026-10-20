/**
 * AguiTransport against the INSTALLED `@ag-ui/client` (0.0.57) over a fake
 * fetch serving real AG-UI SSE framing — no SDK stand-in. Pins the focus
 * integration seams: the `aimms.threadFocus` CUSTOM pass-through (optional
 * callback), and the typed `focus_version_conflict` code classes preserved
 * from RUN_ERROR / HTTP 409 exactly as the backend emits them.
 */
import { afterEach, expect, it, vi } from 'vitest';

import {
  AguiRunError,
  type AguiTurnCallbacks,
  type AguiTurnOptions,
  runAguiTurn
} from './AguiTransport';

function sseResponse(events: unknown[]): Response {
  const body = events
    .map((event) => `data: ${JSON.stringify(event)}\n\n`)
    .join('');
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' }
  });
}

function callbacks(over: Partial<AguiTurnCallbacks> = {}): AguiTurnCallbacks {
  return {
    onTextStart: vi.fn(),
    onTextDelta: vi.fn(),
    onMessagesSnapshot: vi.fn(),
    onToolStart: vi.fn(),
    onToolEnd: vi.fn(),
    onToolStatus: vi.fn(),
    onQuestion: vi.fn(),
    onEntities: vi.fn(),
    onMediaEvidence: vi.fn(),
    onProvenance: vi.fn(),
    onEvidenceAnalysis: vi.fn(),
    onAnalysisProgress: vi.fn(),
    onProposalsRefresh: vi.fn(),
    ...over
  };
}

function turn(over: Partial<AguiTurnOptions> = {}): AguiTurnOptions {
  return {
    url: 'https://app.test/api/ai/agui',
    threadId: 'thread-1',
    message: 'hello',
    idempotencyKey: 'typed:test-1',
    signal: new AbortController().signal,
    callbacks: callbacks(),
    ...over
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

it('dispatches aimms.threadFocus to the optional callback and stays backward compatible', async () => {
  const echo = { focus: null, focus_version: 5, focus_refresh_required: true };
  vi.stubGlobal('fetch', async () =>
    sseResponse([
      { type: 'RUN_STARTED', threadId: 'thread-1', runId: 'run-1' },
      { type: 'CUSTOM', name: 'aimms.threadFocus', value: echo },
      {
        type: 'TEXT_MESSAGE_START',
        messageId: 'm1',
        role: 'assistant'
      },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm1', delta: 'hi' },
      { type: 'TEXT_MESSAGE_END', messageId: 'm1' },
      { type: 'RUN_FINISHED', threadId: 'thread-1', runId: 'run-1' }
    ])
  );
  const cb = callbacks({ onThreadFocus: vi.fn() });
  await runAguiTurn(turn({ callbacks: cb }));
  // Raw pass-through: the recorded snapshot arrives intact and is never
  // re-shaped here (the hook-side normalizer decides what it may do).
  expect(cb.onThreadFocus).toHaveBeenCalledWith(echo);

  // Backward compatible: a callback set WITHOUT the optional consumer runs
  // the same stream cleanly.
  await expect(
    runAguiTurn(turn({ callbacks: callbacks() }))
  ).resolves.toBeUndefined();
});

it('preserves RUN_ERROR code focus_version_conflict as its own failure class', async () => {
  vi.stubGlobal('fetch', async () =>
    sseResponse([
      { type: 'RUN_STARTED', threadId: 'thread-1', runId: 'run-1' },
      {
        type: 'RUN_ERROR',
        message: 'Conversation focus changed. Refresh and retry.',
        code: 'focus_version_conflict'
      }
    ])
  );
  const error = await runAguiTurn(turn()).catch((thrown: unknown) => thrown);
  expect(error).toBeInstanceOf(AguiRunError);
  const runError = error as AguiRunError;
  expect(runError.failureClass).toBe('focus_version_conflict');
  // The server's safe copy survives; no generic retryable is minted.
  expect(runError.message).toContain('Conversation focus changed');
});

it('maps an HTTP 409 focus_version_conflict rejection to the typed class', async () => {
  vi.stubGlobal(
    'fetch',
    async () =>
      new Response(JSON.stringify({ detail: 'focus_version_conflict' }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' }
      })
  );
  const error = await runAguiTurn(turn()).catch((thrown: unknown) => thrown);
  expect(error).toBeInstanceOf(AguiRunError);
  expect((error as AguiRunError).failureClass).toBe('focus_version_conflict');
});

it('forwards the capture-time focus stamp in forwardedProps unchanged', async () => {
  let requestBody: Record<string, unknown> | null = null;
  vi.stubGlobal('fetch', async (_url: string, init: RequestInit) => {
    requestBody = JSON.parse(String(init.body));
    return sseResponse([
      { type: 'RUN_STARTED', threadId: 'thread-1', runId: 'run-1' },
      { type: 'RUN_FINISHED', threadId: 'thread-1', runId: 'run-1' }
    ]);
  });
  await runAguiTurn(turn({ expectedFocusVersion: 42 }));
  expect(
    (requestBody as unknown as { forwardedProps?: Record<string, unknown> })
      ?.forwardedProps?.expectedFocusVersion
  ).toBe(42);
});
