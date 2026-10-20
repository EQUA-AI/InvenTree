import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  type ConversationFocus,
  createFocusRefreshGate,
  fetchThreadFocus,
  focusChipState,
  focusRefOf,
  isFocusKind,
  isFocusRefreshRequired,
  normalizeFocus,
  normalizeFocusRef,
  normalizeThreadFocusEcho,
  normalizeThreadFocusPayload,
  sameFocusRef,
  searchFocusCandidates,
  updateThreadFocus
} from './conversationFocus';

const canonical: ConversationFocus = {
  kind: 'kanban_card',
  id: 11,
  label: 'Replace seal (Card 11)',
  machine_id: 7,
  work_order_id: 5
};

let requests: {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}[];
let responder: (
  url: string,
  method: string
) => { status: number; json: unknown };

beforeEach(() => {
  requests = [];
  responder = () => ({ status: 200, json: { focus: null, focus_version: 0 } });
  vi.stubGlobal('fetch', async (url: string, options: RequestInit) => {
    const method = options.method ?? 'GET';
    requests.push({
      url: String(url),
      method,
      body: options.body ? JSON.parse(String(options.body)) : null
    });
    const result = responder(String(url), method);
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

it('normalizes only canonical projections with strict positive ids', () => {
  expect(normalizeFocus(canonical)).toEqual(canonical);
  expect(normalizeFocus({ ...canonical, id: 0 })).toBeNull();
  expect(normalizeFocus({ ...canonical, id: 1.5 })).toBeNull();
  expect(normalizeFocus({ ...canonical, kind: 'task' })).toBeNull();
  expect(normalizeFocus({ ...canonical, label: undefined })).toBeNull();
  // machine_id/work_order_id may be absent-ish but never fabricated.
  expect(normalizeFocus({ ...canonical, machine_id: '7' })?.machine_id).toBe(
    null
  );
  expect(normalizeFocusRef({ kind: 'machine', id: 3 })).toEqual({
    kind: 'machine',
    id: 3
  });
  expect(normalizeFocusRef({ kind: 'work_order', id: -2 })).toBeNull();
  expect(isFocusKind('kanban_card')).toBe(true);
  expect(isFocusKind('workorder')).toBe(false);
  expect(focusRefOf(canonical)).toEqual({ kind: 'kanban_card', id: 11 });
  expect(
    sameFocusRef({ kind: 'machine', id: 3 }, { kind: 'machine', id: 3 })
  ).toBe(true);
  expect(
    sameFocusRef({ kind: 'machine', id: 3 }, { kind: 'work_order', id: 3 })
  ).toBe(false);
});

it('reads thread focus and reports an untouched thread as not_found', async () => {
  responder = () => ({
    status: 200,
    json: { focus: canonical, focus_version: 4, focus_unavailable: false }
  });
  expect(await fetchThreadFocus('thread-a', 'https://app.test/api/ai')).toEqual(
    {
      ok: true,
      payload: { focus: canonical, focus_version: 4, focus_unavailable: false }
    }
  );
  responder = () => ({ status: 404, json: { detail: 'Thread not found' } });
  expect(await fetchThreadFocus('thread-a', 'https://app.test/api/ai')).toEqual(
    {
      ok: false,
      code: 'not_found'
    }
  );
  // A malformed body is never treated as a confirmed state.
  responder = () => ({ status: 200, json: { focus: { kind: 'machine' } } });
  expect(await fetchThreadFocus('thread-a', 'https://app.test/api/ai')).toEqual(
    {
      ok: false,
      code: 'error'
    }
  );
});

it('preserves unavailable truthfully and keeps it distinct from cleared', () => {
  // Revoked/unavailable: focus null + flag true is preserved as-is.
  expect(
    normalizeThreadFocusPayload({
      focus: null,
      focus_version: 7,
      focus_unavailable: true
    })
  ).toEqual({ focus: null, focus_version: 7, focus_unavailable: true });
  // User-cleared: the paired "cleared control" truth, never unavailable.
  expect(
    normalizeThreadFocusPayload({
      focus: null,
      focus_version: 3,
      focus_unavailable: false
    })
  ).toEqual({ focus: null, focus_version: 3, focus_unavailable: false });
  // Older backend without the flag: compatible `false`, and availability is
  // never invented — a provided `true` is never flipped.
  expect(
    normalizeThreadFocusPayload({ focus: null, focus_version: 2 })
  ).toEqual({ focus: null, focus_version: 2, focus_unavailable: false });
  expect(
    normalizeThreadFocusPayload({
      focus: canonical,
      focus_version: 2,
      focus_unavailable: false
    })
  ).toEqual({ focus: canonical, focus_version: 2, focus_unavailable: false });
  // Malformed flag: rejected entirely, never normalized in.
  expect(
    normalizeThreadFocusPayload({
      focus: null,
      focus_version: 2,
      focus_unavailable: 'true'
    })
  ).toBeNull();
  // Contradiction: an unavailable reference never carries its stale label.
  expect(
    normalizeThreadFocusPayload({
      focus: canonical,
      focus_version: 2,
      focus_unavailable: true
    })
  ).toBeNull();
  // The chip has three distinct truths (availability pairs the clear state).
  expect(focusChipState(null, true)).toBe('unavailable');
  expect(focusChipState(null, false)).toBe('cleared');
  expect(focusChipState(canonical, false)).toBe('target');
});

it('updates the focus with the quoted expected version and returns the ack', async () => {
  responder = () => ({
    status: 200,
    json: { focus: canonical, focus_version: 5, focus_unavailable: false }
  });
  const result = await updateThreadFocus(
    'thread-a',
    'https://app.test/api/ai',
    4,
    {
      kind: 'kanban_card',
      id: 11
    }
  );
  expect(result).toEqual({
    ok: true,
    payload: { focus: canonical, focus_version: 5, focus_unavailable: false }
  });
  expect(requests[0].method).toBe('PUT');
  expect(requests[0].body).toEqual({
    focus: { kind: 'kanban_card', id: 11 },
    expected_focus_version: 4
  });
});

it('clears with focus null and maps conflict/rejection to typed codes', async () => {
  responder = () => ({
    status: 200,
    json: { focus: null, focus_version: 6, focus_unavailable: false }
  });
  expect(
    await updateThreadFocus('thread-a', 'https://app.test/api/ai', 5, null)
  ).toEqual({
    ok: true,
    payload: { focus: null, focus_version: 6, focus_unavailable: false }
  });
  expect(requests[0].body).toEqual({
    focus: null,
    expected_focus_version: 5
  });

  responder = () => ({
    status: 409,
    json: { detail: 'focus_version_conflict' }
  });
  expect(
    await updateThreadFocus('thread-a', 'https://app.test/api/ai', 1, null)
  ).toEqual({ ok: false, code: 'focus_version_conflict' });

  responder = () => ({
    status: 400,
    json: { detail: 'focus_update_rejected' }
  });
  expect(
    await updateThreadFocus('thread-a', 'https://app.test/api/ai', 1, {
      kind: 'machine',
      id: 999
    })
  ).toEqual({ ok: false, code: 'focus_update_rejected' });

  // Malformed-input safe messages are generic rejections (no record probing).
  responder = () => ({ status: 400, json: { detail: 'Unknown focus kind' } });
  expect(
    await updateThreadFocus('thread-a', 'https://app.test/api/ai', 1, {
      kind: 'machine',
      id: 2
    })
  ).toEqual({ ok: false, code: 'focus_update_rejected' });

  responder = () => ({ status: 404, json: { detail: 'Thread not found' } });
  expect(
    await updateThreadFocus('thread-a', 'https://app.test/api/ai', 0, null)
  ).toEqual({ ok: false, code: 'not_found' });
});

it('propagates the safe scope/set-aside refusals verbatim without retry', async () => {
  responder = () => ({
    status: 400,
    json: { detail: 'focus_out_of_analysis_scope' }
  });
  expect(
    await updateThreadFocus('thread-a', 'https://app.test/api/ai', 2, {
      kind: 'work_order',
      id: 9
    })
  ).toEqual({ ok: false, code: 'focus_out_of_analysis_scope' });
  // Exactly one request: a refusal is never auto-retried.
  expect(requests).toHaveLength(1);

  responder = () => ({
    status: 400,
    json: { detail: 'focus_decision_not_set_aside' }
  });
  expect(
    await updateThreadFocus('thread-a', 'https://app.test/api/ai', 2, {
      kind: 'machine',
      id: 7
    })
  ).toEqual({ ok: false, code: 'focus_decision_not_set_aside' });
  expect(requests).toHaveLength(2);
});

it('searches authorized candidates and drops malformed rows', async () => {
  responder = () => ({
    status: 200,
    json: {
      candidates: [canonical, { kind: 'machine' }],
      returned_count: 2,
      has_more: true
    }
  });
  expect(
    await searchFocusCandidates(
      'https://app.test/api/ai',
      'kanban_card',
      'seal',
      5
    )
  ).toEqual({
    ok: true,
    payload: {
      candidates: [canonical],
      returned_count: 2,
      has_more: true
    }
  });
  expect(requests[0].url).toContain('/focus/candidates?');
  expect(requests[0].url).toContain('kind=kanban_card');
  expect(requests[0].url).toContain('query=seal');
  expect(requests[0].url).toContain('limit=5');

  responder = () => ({ status: 500, json: { detail: 'boom' } });
  expect(
    await searchFocusCandidates('https://app.test/api/ai', 'machine', '', 5)
  ).toEqual({ ok: false, code: 'http_500' });
});

it('bounds the candidate limit at the endpoint max and keeps has_more honest', async () => {
  responder = () => ({
    status: 200,
    json: { candidates: [], returned_count: 0, has_more: false }
  });
  await searchFocusCandidates('https://app.test/api/ai', 'machine', '', 50);
  expect(requests.at(-1)?.url).toContain('limit=25');
  await searchFocusCandidates('https://app.test/api/ai', 'machine', '', 0);
  expect(requests.at(-1)?.url).toContain('limit=1');

  // A truncated page can never masquerade as a complete list.
  responder = () => ({
    status: 200,
    json: { candidates: [canonical], returned_count: 1, has_more: 1 }
  });
  const truncated = await searchFocusCandidates(
    'https://app.test/api/ai',
    'machine',
    '',
    10
  );
  expect(truncated.ok && truncated.payload.has_more).toBe(true);
});

it('consumes only a strict refresh signal and never the echoed snapshot', () => {
  // The echo is a RECORDED pre-change snapshot: valid, but only its strict
  // `focus_refresh_required` flag is ever consumed.
  expect(
    normalizeThreadFocusEcho({
      focus: canonical,
      focus_version: 4,
      focus_refresh_required: true
    })
  ).toEqual({
    focus: canonical,
    focus_version: 4,
    focus_refresh_required: true
  });
  expect(
    normalizeThreadFocusEcho({
      focus: null,
      focus_version: 0,
      focus_refresh_required: false
    })
  ).toEqual({ focus: null, focus_version: 0, focus_refresh_required: false });
  // Malformed echoes are never valid signals.
  expect(
    normalizeThreadFocusEcho({
      focus_version: 4,
      focus_refresh_required: 'true'
    })
  ).toBeNull();
  expect(
    normalizeThreadFocusEcho({
      focus_version: -1,
      focus_refresh_required: true
    })
  ).toBeNull();
  expect(
    normalizeThreadFocusEcho({
      focus: canonical,
      focus_version: 4,
      focus_refresh_required: true,
      extra: 'ignored'
    })
  ).not.toBeNull();
  expect(isFocusRefreshRequired({ focus_refresh_required: 1 })).toBe(false);
  expect(isFocusRefreshRequired({ focus_refresh_required: 'true' })).toBe(
    false
  );
  expect(isFocusRefreshRequired({})).toBe(false);
  expect(isFocusRefreshRequired({ focus_refresh_required: true })).toBe(true);
});

it('gates one refresh per live turn and ignores late/aborted events', () => {
  let live = true;
  const gate = createFocusRefreshGate(() => live);
  // False echo / replayed stale version cannot move the chip.
  gate.consume({
    focus: canonical,
    focus_version: 4,
    focus_refresh_required: false
  });
  expect(gate.shouldRefresh()).toBe(false);
  // A strict `true` latches exactly one refresh even when repeated.
  gate.consume({ focus: null, focus_version: 5, focus_refresh_required: true });
  gate.consume({ focus: null, focus_version: 5, focus_refresh_required: true });
  expect(gate.shouldRefresh()).toBe(true);

  // Late / aborted / cross-thread events (turn no longer live) are ignored.
  const late = createFocusRefreshGate(() => false);
  late.consume({ focus: null, focus_version: 6, focus_refresh_required: true });
  expect(late.shouldRefresh()).toBe(false);

  // A malformed loose envelope (STATE_DELTA style) only counts on `=== true`.
  const loose = createFocusRefreshGate(() => true);
  loose.consume({ kind: 'thread_focus', focus_refresh_required: 1 });
  expect(loose.shouldRefresh()).toBe(false);
  loose.consume({ kind: 'thread_focus', focus_refresh_required: true });
  expect(loose.shouldRefresh()).toBe(true);
  live = false;
});

it('never lets a malformed complete echo bypass strict normalization via the loose fallback', () => {
  // A COMPLETE wire echo record (focus + focus_version present) claims to be
  // authoritative: when strict normalization rejects it, the loose legacy
  // true-flag reader must NOT resurrect it as a refresh signal.
  const gate = createFocusRefreshGate(() => true);
  gate.consume({
    focus: { kind: 'machine' },
    focus_version: 'invalid',
    focus_refresh_required: true
  });
  expect(gate.shouldRefresh()).toBe(false);
});
