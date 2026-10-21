/**
 * VOICE-H06 browser fixture — MOCKED NETWORK, REAL HOOK.
 *
 * Mounts the ACTUAL consuming hook (`useAIChat`) so the summary-scope
 * hydration path under test is the production one (applyScopeSummary →
 * hydrateActiveScopeSummary → full authorized scope GET). Only `window.fetch`
 * is stubbed: the scope GET (`/threads/<id>/scope`) is deferrable so the spec
 * can replay the exact VOICE-H06 orderings (stale read after newer write,
 * thread/selection/account switch mid-flight, failed/revoked/null response).
 * Every response body is a full authentic wire shape (ThreadScopePayload /
 * ActiveScopeSummary with mode `explicit_assets` — the generated
 * AnalysisScopeMode has no 'machines' member).
 * This is MOCKED BROWSER RENDERING — not a live backend E2E run.
 */
import './demo-metrics-lingui';

import { MantineProvider } from '@mantine/core';
import '@mantine/core/styles.css';
import { createRoot } from 'react-dom/client';

import type {
  AnalysisScopeUpdate,
  ThreadScopePayload
} from '../lib/types/AimmsWire.generated';
import { useAIChat } from '../src/hooks/UseAIChat';
import { useUserState } from '../src/states/UserState';
import { emptyThreadFocusPayload } from '../tests/pages/focus_fixture';

useUserState.setState({
  is_authed: true,
  authStatus: 'authenticated',
  user: {
    pk: 11,
    username: 'Voice deferred focus fixture',
    is_superuser: true,
    userId: () => (window as any).__fixtureUserId ?? 11
  } as any
});

type Deferred = {
  url: string;
  method: string;
  accountUserId: number | undefined;
  resolve: (body: unknown, status: number) => void;
};

const jsonResponse = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' }
  });

const summaryPayload = {
  mode: 'explicit_assets',
  version: 4,
  display_label: 'Fixture 12'
};

const net = {
  // 'payload' | 'denied' | 'defer' — how the scope GET responds.
  scopeMode: 'defer' as 'payload' | 'denied' | 'defer',
  scopeBodies: {} as Record<string, ThreadScopePayload>,
  shared: {} as Record<string, boolean>,
  summaries: {} as Record<string, unknown>,
  // Optional override for the thread-list (syncThreads) response.
  listBody: null as Record<string, unknown> | null,
  pending: [] as Deferred[],
  accountFlip: () => {
    const next = ((window as any).__fixtureUserId ?? 11) === 11 ? 22 : 11;
    (window as any).__fixtureUserId = next;
    // A REAL user-store update (new user object, new pk) so useAIChat
    // rerenders and recomputes its account cache key
    // chatIndexKey(backendHost, user.userId()) where `user` is the STORE
    // (useUserState()) and userId() derives from the user record — the hook's
    // true account boundary. The whole identity flips together (pk +
    // userId()); a bare global or a userId-only flip leaves the key unchanged
    // and never crosses the boundary.
    useUserState.setState((prev: any) => ({
      user: { ...prev.user, pk: next, userId: () => next }
    }));
  }
};
(window as any).__scopeNet = net;

const scopePayload = (threadId: string): ThreadScopePayload =>
  net.scopeBodies[threadId] ?? {
    thread_id: threadId,
    version: 4,
    hash: `fixture-${threadId}-v4`,
    display_label: 'Fixture 12',
    editable: !net.shared[threadId],
    scope: {
      schema_version: 1,
      mode: 'explicit_assets',
      machine_ids: [12, 15],
      date_window: { from: null, to: null },
      source_classes: [],
      display_label: 'Fixture 12'
    }
  };

window.fetch = ((input: unknown, init?: RequestInit) => {
  const url = String(input);
  const method = (init?.method ?? 'GET').toUpperCase();
  if (/\/threads\/[^/]+\/focus$/.test(url) && method === 'GET') {
    return Promise.resolve(jsonResponse(emptyThreadFocusPayload, 200));
  }
  const scopeMatch = url.match(/\/threads\/([^/]+)\/scope$/);
  if (scopeMatch) {
    const threadId = decodeURIComponent(scopeMatch[1]);
    if (method === 'GET') {
      if (net.scopeMode === 'denied') {
        return Promise.resolve(jsonResponse({ detail: 'not_found' }, 404));
      }
      if (net.scopeMode === 'payload') {
        return Promise.resolve(jsonResponse(scopePayload(threadId), 200));
      }
      return new Promise<Response>((resolve) => {
        net.pending.push({
          url,
          method,
          accountUserId: useUserState.getState().userId(),
          resolve: (body, status) => resolve(jsonResponse(body, status))
        });
      });
    }
    // Scope PUT: apply and ack immediately (a "newer scope write").
    // Real body shape: { expected_version, scope: AnalysisScopeUpdate }.
    const body: {
      expected_version?: number;
      scope?: AnalysisScopeUpdate;
    } = JSON.parse(String(init?.body ?? '{}'));
    const update: AnalysisScopeUpdate = body.scope ?? {
      mode: 'explicit_assets',
      machine_ids: [13]
    };
    const next: ThreadScopePayload = {
      thread_id: threadId,
      version: (body.expected_version ?? 0) + 1,
      hash: `fixture-${threadId}-ack`,
      display_label: 'Fixture 13',
      editable: !net.shared[threadId],
      scope: {
        schema_version: 1,
        mode: update.mode,
        machine_ids: update.machine_ids ?? [],
        date_window: update.date_window ?? { from: null, to: null },
        source_classes: update.source_classes ?? [],
        display_label: 'Fixture 13'
      }
    };
    net.scopeBodies[threadId] = next;
    return Promise.resolve(jsonResponse(next, 200));
  }
  const detailMatch = url.match(/\/threads\/([^/?]+)(\?|$)/);
  if (detailMatch && method === 'GET') {
    const threadId = decodeURIComponent(detailMatch[1]);
    return Promise.resolve(
      jsonResponse(
        {
          messages: [],
          title: `Thread ${threadId}`,
          created_at: '2026-10-06T00:00:00Z',
          updated_at: '2026-10-06T00:00:00Z',
          active_scope: net.summaries[threadId] ?? summaryPayload,
          shared: Boolean(net.shared[threadId]),
          has_earlier: false
        },
        200
      )
    );
  }
  return Promise.resolve(
    jsonResponse(
      net.listBody ?? { threads: [], sync_token: null, has_more: false },
      200
    )
  );
}) as typeof fetch;

function Probe() {
  const chat = useAIChat();
  // Observe the REAL user-store rerender: this value only changes when the
  // store update rerenders the tree (the hook then recomputes its account
  // cache key chatIndexKey(backendHost, user.userId())).
  const accountUserId = useUserState((s) => s.userId());
  (window as any).__chat = chat;
  // Real hook exports only — no invented fallbacks.
  (window as any).__switchThread = (id: string) => chat.switchThread(id);
  (window as any).__syncThreads = () => chat.syncThreads();
  return (
    <pre id='state'>
      {JSON.stringify({
        activeThreadId: chat.activeThreadId,
        activeThreadShared: chat.activeThreadShared,
        activeScope: chat.activeScope,
        accountUserId
      })}
    </pre>
  );
}

createRoot(document.getElementById('root')!).render(
  <MantineProvider>
    <Probe />
  </MantineProvider>
);
