import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type {
  ConversationFocus,
  ThreadFocusPayload
} from '../components/aichat/conversationFocus';
import { useThreadFocusState } from './ThreadFocusState';

type Reply = { status: number; json: unknown };
const store = () => useThreadFocusState.getState();
const host = 'https://app.test/api/ai';
const workOrder: ConversationFocus = {
  kind: 'work_order',
  id: 42,
  label: 'WO-0042: exact entry acknowledgement',
  machine_id: 17,
  work_order_id: 42
};
const card: ConversationFocus = {
  kind: 'kanban_card',
  id: 77,
  label: 'Task 77: newer visible selection',
  machine_id: 17,
  work_order_id: 42
};
const ownAck: ThreadFocusPayload = {
  focus: workOrder,
  focus_version: 1,
  focus_unavailable: false
};
const newerAck: ThreadFocusPayload = {
  focus: card,
  focus_version: 2,
  focus_unavailable: false
};
let responder: (method: string) => Reply | Promise<Reply>;
let puts: Record<string, unknown>[];

beforeEach(() => {
  store().unbind();
  store().bind({ threadId: 'ack-thread-a', shared: false, host });
  store().applyAck({ focus: null, focus_version: 0, focus_unavailable: false });
  puts = [];
  responder = () => ({ status: 200, json: ownAck });
  vi.stubGlobal('fetch', async (_url: string, options: RequestInit) => {
    const method = options.method ?? 'GET';
    if (method === 'PUT') {
      puts.push(JSON.parse(String(options.body)));
    }
    const result = await responder(method);
    return {
      ok: result.status >= 200 && result.status < 300,
      status: result.status,
      json: async () => result.json
    };
  });
});

afterEach(() => {
  store().unbind();
  vi.unstubAllGlobals();
});

it('RV09 publishes the ordered entry own ACK once while preserving its existing return shape', async () => {
  const acknowledged = vi.fn<(version: number) => void>();
  const result = await store().select(
    { kind: 'work_order', id: 42 },
    acknowledged
  );
  expect(result).toEqual({ ok: true });
  expect(puts).toEqual([
    { focus: { kind: 'work_order', id: 42 }, expected_focus_version: 0 }
  ]);
  expect(acknowledged).toHaveBeenCalledTimes(1);
  expect(acknowledged).toHaveBeenCalledWith(1);
  expect(store().focus).toEqual(workOrder);
  expect(store().version).toBe(1);
});

it('RV09 publishes held own ACK1 without borrowing or rolling back the newer global ACK2', async () => {
  let release: (reply: Reply) => void = () => {
    throw new Error('PUT barrier not established');
  };
  responder = () =>
    new Promise<Reply>((resolve) => {
      release = resolve;
    });
  const acknowledged = vi.fn<(version: number) => void>();
  const pending = store().select({ kind: 'work_order', id: 42 }, acknowledged);
  expect(puts).toHaveLength(1);
  store().applyAck(newerAck, 'ack-thread-a');
  expect(store().focus).toEqual(card);
  expect(store().version).toBe(2);
  expect(acknowledged).not.toHaveBeenCalled();
  release({ status: 200, json: ownAck });
  expect(await pending).toEqual({ ok: true });
  expect(acknowledged).toHaveBeenCalledTimes(1);
  expect(acknowledged).toHaveBeenCalledWith(1);
  expect(store().focus).toEqual(card);
  expect(store().version).toBe(2);
  expect(store().status).toBe('ready');
});

it('RV09 never publishes an old entry ACK after the binding generation changes', async () => {
  let release: (reply: Reply) => void = () => {
    throw new Error('PUT barrier not established');
  };
  responder = () =>
    new Promise<Reply>((resolve) => {
      release = resolve;
    });
  const acknowledged = vi.fn<(version: number) => void>();
  const pending = store().select({ kind: 'work_order', id: 42 }, acknowledged);
  expect(puts).toHaveLength(1);
  store().bind({ threadId: 'ack-thread-b', shared: false, host });
  store().applyAck(newerAck, 'ack-thread-b');
  release({ status: 200, json: ownAck });
  expect(await pending).toEqual({ ok: false, code: 'context_changed' });
  expect(acknowledged).not.toHaveBeenCalled();
  expect(store().binding?.threadId).toBe('ack-thread-b');
  expect(store().focus).toEqual(card);
  expect(store().version).toBe(2);
});

it.each([
  { status: 400, code: 'focus_update_rejected' },
  { status: 409, code: 'focus_version_conflict' }
])(
  'RV09 never publishes an ACK for refused focus PUT $status',
  async ({ status, code }) => {
    responder = (method) =>
      method === 'PUT'
        ? { status, json: { detail: code } }
        : { status: 200, json: newerAck };
    const acknowledged = vi.fn<(version: number) => void>();
    expect(
      await store().select({ kind: 'work_order', id: 42 }, acknowledged)
    ).toEqual({ ok: false, code });
    expect(puts).toHaveLength(1);
    expect(acknowledged).not.toHaveBeenCalled();
    expect(store().focus).toEqual(status === 409 ? card : null);
    expect(store().version).toBe(status === 409 ? 2 : 0);
  }
);
