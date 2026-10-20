import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ConversationFocus } from '../components/aichat/conversationFocus';
import { useThreadFocusState } from './ThreadFocusState';

const machine: ConversationFocus = {
  kind: 'machine',
  id: 7,
  label: 'Pump A',
  machine_id: 7,
  work_order_id: null
};
const card: ConversationFocus = {
  kind: 'kanban_card',
  id: 11,
  label: 'Replace seal (Card 11)',
  machine_id: 7,
  work_order_id: 5
};
const workOrder: ConversationFocus = {
  kind: 'work_order',
  id: 5,
  label: 'WO-000005 Pump service',
  machine_id: 7,
  work_order_id: 5
};

let responder: (
  url: string,
  method: string
) =>
  | { status: number; json: unknown }
  | Promise<{ status: number; json: unknown }>;
let requests: {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}[];

const store = () => useThreadFocusState.getState();

beforeEach(() => {
  useThreadFocusState.setState({
    binding: null,
    status: 'idle',
    focus: null,
    unavailable: false,
    version: null,
    error: null
  });
  requests = [];
  responder = () => ({ status: 404, json: { detail: 'Thread not found' } });
  vi.stubGlobal('fetch', async (url: string, options: RequestInit) => {
    const method = options.method ?? 'GET';
    requests.push({
      url: String(url),
      method,
      body: options.body ? JSON.parse(String(options.body)) : null
    });
    const result = await responder(String(url), method);
    return {
      ok: result.status >= 200 && result.status < 300,
      status: result.status,
      json: async () => result.json
    };
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

it('binds a thread and materializes its focus state from the server', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  expect(store().focus).toBeNull();
  expect(store().version).toBeNull();
  responder = () => ({
    status: 200,
    json: { focus: machine, focus_version: 3 }
  });
  await store().refresh();
  expect(store().focus).toEqual(machine);
  expect(store().version).toBe(3);
  expect(store().status).toBe('ready');
});

it('reports an untouched thread as no focus with no observed version', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  await store().refresh();
  // 404 = no server row: no focus and NO version stamp is ever invented.
  expect(store().focus).toBeNull();
  expect(store().version).toBeNull();
  expect(store().status).toBe('ready');
});

it('selects each target kind through the owner PUT and applies the exact ack', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  for (const [ref, ack] of [
    [
      { kind: 'machine', id: 7 },
      { focus: machine, focus_version: 1, focus_unavailable: false }
    ],
    [
      { kind: 'kanban_card', id: 11 },
      { focus: card, focus_version: 2, focus_unavailable: false }
    ],
    [
      { kind: 'work_order', id: 5 },
      { focus: workOrder, focus_version: 3, focus_unavailable: false }
    ]
  ] as const) {
    responder = () => ({ status: 200, json: ack });
    const result = await store().select(ref);
    expect(result).toEqual({ ok: true });
    expect(requests.at(-1)?.method).toBe('PUT');
    expect(requests.at(-1)?.body).toEqual({
      focus: ref,
      expected_focus_version: ack.focus_version - 1
    });
    expect(store().focus).toEqual(ack.focus);
    expect(store().version).toBe(ack.focus_version);
    expect(store().status).toBe('ready');
  }
});

it('clears the focus safely and never touches scope state', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  responder = () => ({
    status: 200,
    json: { focus: machine, focus_version: 2 }
  });
  await store().select({ kind: 'machine', id: 7 });
  responder = () => ({ status: 200, json: { focus: null, focus_version: 3 } });
  expect(await store().select(null)).toEqual({ ok: true });
  expect(requests.at(-1)?.body).toEqual({
    focus: null,
    expected_focus_version: 2
  });
  expect(store().focus).toBeNull();
  expect(store().version).toBe(3);
});

it('keeps the previous focus on an unauthorized or conflicting update', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  responder = () => ({
    status: 200,
    json: { focus: machine, focus_version: 2 }
  });
  await store().select({ kind: 'machine', id: 7 });

  responder = () => ({
    status: 400,
    json: { detail: 'focus_update_rejected' }
  });
  expect(await store().select({ kind: 'work_order', id: 99 })).toEqual({
    ok: false,
    code: 'focus_update_rejected'
  });
  expect(store().focus).toEqual(machine);
  expect(store().version).toBe(2);
  expect(store().status).toBe('ready');

  responder = () => ({
    status: 409,
    json: { detail: 'focus_version_conflict' }
  });
  expect(await store().select({ kind: 'machine', id: 9 })).toEqual({
    ok: false,
    code: 'focus_version_conflict'
  });
  // State stays untouched by the rejected write (the refresh may converge
  // later, but nothing client-side invents the winner).
  expect(store().focus).toEqual(machine);
  expect(store().version).toBe(2);
});

it('never mutates a read-only shared thread', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: true,
    host: 'https://app.test/api/ai'
  });
  responder = () => ({
    status: 200,
    json: { focus: machine, focus_version: 1 }
  });
  await store().refresh();
  const before = requests.length;
  expect(await store().select({ kind: 'machine', id: 7 })).toEqual({
    ok: false,
    code: 'shared-thread'
  });
  expect(await store().select(null)).toEqual({
    ok: false,
    code: 'shared-thread'
  });
  expect(requests.length).toBe(before);
});

it('isolates threads: a late ack for the old thread can never move the new chip', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  let release: (value: { status: number; json: unknown }) => void = () => {};
  responder = () =>
    new Promise((resolve) => {
      release = resolve;
    });
  const pending = store().select({ kind: 'machine', id: 7 });
  // The visible thread switches while the focus PUT is still in flight.
  store().bind({
    threadId: 'thread-b',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  release({ status: 200, json: { focus: machine, focus_version: 9 } });
  expect(await pending).toEqual({ ok: false, code: 'context_changed' });
  expect(store().focus).toBeNull();
  expect(store().version).toBeNull();
  expect(store().binding?.threadId).toBe('thread-b');
});

it('drops a refresh that resolves after a thread switch', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  let release: (value: { status: number; json: unknown }) => void = () => {};
  responder = () =>
    new Promise((resolve) => {
      release = resolve;
    });
  const pending = store().refresh();
  store().bind({
    threadId: 'thread-b',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  release({ status: 200, json: { focus: workOrder, focus_version: 4 } });
  await pending;
  expect(store().focus).toBeNull();
  expect(store().binding?.threadId).toBe('thread-b');
});

it('requires a bound thread before any write', async () => {
  expect(await store().select({ kind: 'machine', id: 7 })).toEqual({
    ok: false,
    code: 'no-thread'
  });
  expect(requests).toHaveLength(0);
});

it('keeps a newer acknowledged selection when an older same-thread read resolves late', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  let releaseRead: (value: { status: number; json: unknown }) => void =
    () => {};
  responder = (_url, method) => {
    if (method === 'PUT') {
      return {
        status: 200,
        json: { focus: card, focus_version: 2, focus_unavailable: false }
      };
    }
    return new Promise((resolve) => {
      releaseRead = resolve;
    });
  };
  const pending = store().refresh();
  // The newer owner PUT is acknowledged first (machine @ v1 -> card @ v2).
  expect(await store().select({ kind: 'kanban_card', id: 11 })).toEqual({
    ok: true
  });
  expect(store().focus).toEqual(card);
  expect(store().version).toBe(2);
  // The older GET, captured before the PUT, resolves afterwards: it must
  // never roll the confirmed identity/version back to machine @ v1.
  releaseRead({ status: 200, json: { focus: machine, focus_version: 1 } });
  await pending;
  expect(store().focus).toEqual(card);
  expect(store().version).toBe(2);
  expect(store().status).toBe('ready');
});

it('never unseats a confirmed focus with a stale same-thread 404 read', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  let releaseRead: (value: { status: number; json: unknown }) => void =
    () => {};
  responder = (_url, method) => {
    if (method === 'PUT') {
      return {
        status: 200,
        json: { focus: card, focus_version: 2, focus_unavailable: false }
      };
    }
    return new Promise((resolve) => {
      releaseRead = resolve;
    });
  };
  const pending = store().refresh();
  expect(await store().select({ kind: 'kanban_card', id: 11 })).toEqual({
    ok: true
  });
  // The stale no-row 404 (captured before the PUT) must not clear the acked
  // identity/version, and the dropped read is never left trapped in
  // 'loading'.
  releaseRead({ status: 404, json: { detail: 'Thread not found' } });
  await pending;
  expect(store().focus).toEqual(card);
  expect(store().version).toBe(2);
  expect(store().status).toBe('ready');
});

it('applies no-row/denial handling to a fresh 404 after a settled read', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  responder = () => ({
    status: 200,
    json: { focus: card, focus_version: 2, focus_unavailable: false }
  });
  await store().refresh();
  expect(store().focus).toEqual(card);
  expect(store().version).toBe(2);
  // No outstanding read and no intervening write: this 404 is authoritative
  // (no server row, or a thread-level access denial — the backend
  // re-authorizes every read) and must never be treated as stale just
  // because a version was once observed.
  responder = () => ({ status: 404, json: { detail: 'Thread not found' } });
  await store().refresh();
  expect(store().focus).toBeNull();
  expect(store().unavailable).toBe(false);
  expect(store().version).toBeNull();
  expect(store().status).toBe('ready');
});

it('never lets an older equal-version read overwrite a newer unavailable read', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  let releaseOlder: (value: { status: number; json: unknown }) => void =
    () => {};
  let first = true;
  responder = () => {
    if (first) {
      first = false;
      return new Promise((resolve) => {
        releaseOlder = resolve;
      });
    }
    return {
      status: 200,
      json: { focus: null, focus_version: 1, focus_unavailable: true }
    };
  };
  const older = store().refresh();
  await store().refresh();
  // The newer read — at the SAME stored focus_version (the backend
  // re-authorizes every read as the current actor and may report
  // unavailable without incrementing the version) — says the target is
  // gone for this reader.
  expect(store().focus).toBeNull();
  expect(store().unavailable).toBe(true);
  expect(store().version).toBe(1);
  // The OLDER read resolves afterwards at the same version carrying the
  // stale available label: real read ordering fences it out — availability
  // is never rolled back by an equal-version older read.
  releaseOlder({
    status: 200,
    json: { focus: machine, focus_version: 1, focus_unavailable: false }
  });
  await older;
  expect(store().focus).toBeNull();
  expect(store().unavailable).toBe(true);
  expect(store().version).toBe(1);
  expect(store().status).toBe('ready');
});

it('keeps unavailable distinct from cleared and clears it on rebind', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  responder = () => ({
    status: 200,
    json: { focus: null, focus_version: 4, focus_unavailable: true }
  });
  await store().refresh();
  expect(store().focus).toBeNull();
  expect(store().unavailable).toBe(true);
  expect(store().version).toBe(4);
  // Paired cleared control: a user-cleared focus is a different truth.
  responder = () => ({
    status: 200,
    json: { focus: null, focus_version: 5, focus_unavailable: false }
  });
  await store().refresh();
  expect(store().focus).toBeNull();
  expect(store().unavailable).toBe(false);
  // Defaults and generation clear correctly on a thread rebind.
  store().bind({
    threadId: 'thread-b',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  expect(store().unavailable).toBe(false);
  expect(store().version).toBeNull();
  store().unbind();
  expect(store().unavailable).toBe(false);
});

it('drops a late unavailable ack after a thread switch', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  let release: (value: { status: number; json: unknown }) => void = () => {};
  responder = () =>
    new Promise((resolve) => {
      release = resolve;
    });
  const pending = store().refresh();
  store().bind({
    threadId: 'thread-b',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  release({
    status: 200,
    json: { focus: null, focus_version: 9, focus_unavailable: true }
  });
  await pending;
  expect(store().unavailable).toBe(false);
  expect(store().version).toBeNull();
  expect(store().binding?.threadId).toBe('thread-b');
});

it('refuses out-of-scope and failed set-aside without touching state or retrying', async () => {
  store().bind({
    threadId: 'thread-a',
    shared: false,
    host: 'https://app.test/api/ai'
  });
  responder = () => ({
    status: 200,
    json: { focus: machine, focus_version: 2, focus_unavailable: false }
  });
  await store().select({ kind: 'machine', id: 7 });
  const afterSelect = requests.length;

  responder = () => ({
    status: 400,
    json: { detail: 'focus_out_of_analysis_scope' }
  });
  expect(await store().select({ kind: 'work_order', id: 9 })).toEqual({
    ok: false,
    code: 'focus_out_of_analysis_scope'
  });
  // Exactly one request — a refusal never changes focus/scope and never
  // retries automatically.
  expect(requests.length).toBe(afterSelect + 1);
  expect(store().focus).toEqual(machine);
  expect(store().version).toBe(2);

  responder = () => ({
    status: 400,
    json: { detail: 'focus_decision_not_set_aside' }
  });
  expect(await store().select({ kind: 'machine', id: 9 })).toEqual({
    ok: false,
    code: 'focus_decision_not_set_aside'
  });
  // A failed set-aside consumes nothing and is never retried.
  expect(requests.length).toBe(afterSelect + 2);
  expect(store().focus).toEqual(machine);
  expect(store().unavailable).toBe(false);
});

it.each([false, true])(
  'keeps the newer selection after an older PUT acknowledgement (delayed=%s)',
  async (delayed) => {
    store().bind({
      threadId: 'thread-a',
      shared: false,
      host: 'https://app.test/api/ai'
    });
    store().applyAck({
      focus: machine,
      focus_version: 1,
      focus_unavailable: false
    });
    let releaseOld: (value: { status: number; json: unknown }) => void =
      () => {};
    let firstWrite = true;
    responder = (_url, method) => {
      if (method === 'GET') {
        // The server has applied the old write even if its PUT ack is held.
        return { status: 200, json: { focus: card, focus_version: 2 } };
      }
      if (firstWrite) {
        firstWrite = false;
        if (delayed) {
          return new Promise((resolve) => {
            releaseOld = resolve;
          });
        }
        return { status: 200, json: { focus: card, focus_version: 2 } };
      }
      return { status: 200, json: { focus: workOrder, focus_version: 3 } };
    };
    const oldWrite = store().select({ kind: 'kanban_card', id: 11 });
    if (!delayed) await oldWrite;
    await store().refresh();
    expect(store().focus).toEqual(card);
    expect(store().version).toBe(2);
    expect(await store().select({ kind: 'work_order', id: 5 })).toEqual({
      ok: true
    });
    if (delayed) {
      releaseOld({ status: 200, json: { focus: card, focus_version: 2 } });
      await oldWrite;
    }
    expect(requests.map((request) => request.method)).toEqual([
      'PUT',
      'GET',
      'PUT'
    ]);
    expect(store().focus).toEqual(workOrder);
    expect(store().version).toBe(3);
    expect(store().error).toBeNull();
    expect(store().status).toBe('ready');
  }
);

it.each([
  { status: 422, code: 'focus_update_rejected' },
  { status: 409, code: 'focus_version_conflict' }
])(
  'drops a stale PUT $status error without changing the newer selection or refreshing',
  async ({ status, code }) => {
    store().bind({
      threadId: 'thread-a',
      shared: false,
      host: 'https://app.test/api/ai'
    });
    store().applyAck({
      focus: machine,
      focus_version: 1,
      focus_unavailable: false
    });
    let releaseOld: (value: { status: number; json: unknown }) => void =
      () => {};
    let firstWrite = true;
    responder = () => {
      if (firstWrite) {
        firstWrite = false;
        return new Promise((resolve) => {
          releaseOld = resolve;
        });
      }
      return { status: 200, json: { focus: workOrder, focus_version: 3 } };
    };
    const oldWrite = store().select({ kind: 'kanban_card', id: 11 });
    expect(await store().select({ kind: 'work_order', id: 5 })).toEqual({
      ok: true
    });
    releaseOld({ status, json: { detail: code } });
    expect(await oldWrite).toEqual({ ok: false, code });
    expect(requests).toHaveLength(2);
    expect(store().focus).toEqual(workOrder);
    expect(store().version).toBe(3);
    expect(store().error).toBeNull();
    expect(store().status).toBe('ready');
  }
);
