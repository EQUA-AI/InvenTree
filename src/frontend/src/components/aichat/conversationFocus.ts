/**
 * Conversation focus: generated wire aliases + owner-rail HTTP (plan package
 * C → sequential client integration against the real backend wire).
 *
 * The canonical wire types are GENERATED (`AimmsWire.generated.ts`, backend
 * `ai.core.focus.wire` / `manage.py generate_wire_contract`). This module
 * never redeclares a wire shape: the declarations below are re-exports /
 * aliases of the generated ones (keeping the names the frontend already
 * imports). The artifact itself is never hand-edited.
 *
 * Contract (LocalDocs/VoiceFocusBackendHandoff.md §1 / VoiceFocusIntegration
 * Handoff.md §2):
 * - `GET  /api/ai/threads/{id}/focus` -> ThreadFocusPayload (404 = no row).
 *   The read path always carries `focus_unavailable: bool`: a stored target
 *   that fails per-READER re-authorization returns `focus: null` +
 *   `focus_unavailable: true` (a stale label is never disclosed; shared
 *   thread readers are re-authorized as themselves). Write echoes carry
 *   `false`.
 * - `PUT  /api/ai/threads/{id}/focus` body ThreadFocusUpdateRequest ->
 *   ThreadFocusPayload; 409 `focus_version_conflict`; 400 typed refusals
 *   `focus_update_rejected` (generic: unknown/unauthorized/unavailable),
 *   `focus_out_of_analysis_scope` (authorized target outside an explicit
 *   analysis scope — state and scope version unchanged) and
 *   `focus_decision_not_set_aside` (an ARMED decision could not be set
 *   aside — fail closed, nothing written). State is unchanged on every
 *   error; nothing is ever auto-retried.
 * - `GET  /api/ai/focus/candidates?kind=&query=&limit=1..25` ->
 *   FocusCandidateSearchResult (search hints never authorize; `has_more` is
 *   real server truncation).
 *
 * A candidate `{kind, id}` is never a grant: the server re-resolves and
 * re-authorizes every reference and returns the canonical projection
 * (`label`, `machine_id`, `work_order_id`). Client labels are display-only.
 *
 * Turn echo (`aimms.threadFocus` CUSTOM value, `ChatResponse`,
 * `VoiceTurnResponse`): the turn's RECORDED pre-change focus binding — never
 * applied to the server-confirmed chip. `focus_refresh_required: true` (and
 * only a strict boolean `true`) means the turn CHANGED focus: re-read
 * `GET /threads/{id}/focus` once.
 */
import type {
  ConversationFocusKind,
  ConversationFocusPayload,
  ConversationFocusRef,
  FocusErrorCode,
  FocusCandidateSearchResult as GeneratedFocusCandidateSearchResult,
  ThreadFocusPayload,
  ThreadFocusUpdateRequest
} from '@lib/types/AimmsWire.generated';

export type {
  ConversationFocusKind,
  ConversationFocusRef,
  FocusErrorCode,
  ThreadFocusPayload,
  ThreadFocusUpdateRequest
};

/** The canonical, server-resolved focus projection (hash-free, version-free). */
export type ConversationFocus = ConversationFocusPayload;

/** `GET /focus/candidates` response body (generated `FocusCandidateSearchResult`). */
export type FocusCandidatesPayload = GeneratedFocusCandidateSearchResult;

export const CONVERSATION_FOCUS_KINDS: readonly ConversationFocusKind[] = [
  'machine',
  'kanban_card',
  'work_order'
];

/** The generated `FocusErrorCode` members, verbatim (runtime narrowing). */
const FOCUS_ERROR_CODES: readonly string[] = [
  'focus_version_conflict',
  'focus_update_rejected',
  'focus_out_of_analysis_scope',
  'focus_decision_not_set_aside'
];

/**
 * Per-turn stale focus stamp code (in the backend's
 * `SERVER_VOICE_ERROR_CODES` / typed RUN_ERROR codes).
 */
export const VOICE_FOCUS_CHANGED = 'VOICE_FOCUS_CHANGED';

export function isFocusKind(value: unknown): value is ConversationFocusKind {
  return (
    typeof value === 'string' &&
    (CONVERSATION_FOCUS_KINDS as readonly string[]).includes(value)
  );
}

export function isFocusErrorCode(value: unknown): value is FocusErrorCode {
  return typeof value === 'string' && FOCUS_ERROR_CODES.includes(value);
}

/** Strict positive integer ids only — malformed input is never normalized in. */
export function isFocusId(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

export function normalizeFocusRef(raw: unknown): ConversationFocusRef | null {
  const candidate = raw as { kind?: unknown; id?: unknown } | null;
  if (!candidate || typeof candidate !== 'object') return null;
  if (!isFocusKind(candidate.kind) || !isFocusId(candidate.id)) return null;
  return { kind: candidate.kind, id: candidate.id };
}

/**
 * Fail-closed projection normalization: an unknown shape never becomes a
 * displayed "confirmed" target (no guessed or label-only matches).
 */
export function normalizeFocus(raw: unknown): ConversationFocus | null {
  const candidate = raw as {
    kind?: unknown;
    id?: unknown;
    label?: unknown;
    machine_id?: unknown;
    work_order_id?: unknown;
  } | null;
  const ref = normalizeFocusRef(candidate);
  if (!ref || !candidate) return null;
  if (typeof candidate.label !== 'string') return null;
  return {
    kind: ref.kind,
    id: ref.id,
    label: candidate.label,
    machine_id: isFocusId(candidate.machine_id) ? candidate.machine_id : null,
    work_order_id: isFocusId(candidate.work_order_id)
      ? candidate.work_order_id
      : null
  };
}

/**
 * Normalize `GET/PUT /threads/{id}/focus` into the generated
 * `ThreadFocusPayload`, preserving `focus_unavailable` truthfully:
 * - `focus_unavailable: true` is preserved and only valid with
 *   `focus: null` (the revoked/unavailable state — a stale label is never
 *   disclosed and availability is never invented);
 * - a non-boolean flag, or `true` alongside a non-null focus, is
 *   contradictory/malformed → the payload is rejected entirely;
 * - a MISSING flag is an older backend: compatible default `false` only —
 *   it can never turn a provided `true` into availability.
 * A user-cleared focus (`focus: null`, `focus_unavailable: false`) stays
 * distinct from the unavailable state.
 */
export function normalizeThreadFocusPayload(
  raw: unknown
): ThreadFocusPayload | null {
  const candidate = raw as {
    focus?: unknown;
    focus_version?: unknown;
    focus_unavailable?: unknown;
  } | null;
  if (!candidate || typeof candidate !== 'object') return null;
  const version = candidate.focus_version;
  if (typeof version !== 'number' || !Number.isInteger(version) || version < 0)
    return null;
  const flag = candidate.focus_unavailable;
  if (flag !== undefined && typeof flag !== 'boolean') return null;
  // `focus` is REQUIRED: a missing/undefined property is malformed data and
  // never an implicit clear — only an explicit `focus: null` clears state.
  if (candidate.focus === undefined) return null;
  const focus = candidate.focus ?? null;
  const normalizedFocus = normalizeFocus(focus);
  if (focus !== null && normalizedFocus === null) return null;
  const focusUnavailable = flag === undefined ? false : flag;
  // Contradiction: an unavailable reference cannot also carry its label.
  if (focusUnavailable && normalizedFocus !== null) return null;
  return {
    focus: normalizedFocus,
    focus_version: version,
    focus_unavailable: focusUnavailable
  };
}

/**
 * The turn's RECORDED focus echo (`aimms.threadFocus` CUSTOM value,
 * `ChatResponse`, `VoiceTurnResponse`): the PRE-change binding snapshot (the
 * original snapshot on replay) — never a claim about current thread state.
 * Validated strictly; only `focus_refresh_required` is ever consumed (as a
 * refresh signal), and the echoed `focus` is never applied to the chip.
 */
export interface ThreadFocusEcho {
  focus: ConversationFocus | null;
  focus_version: number;
  focus_refresh_required: boolean;
}

export function normalizeThreadFocusEcho(raw: unknown): ThreadFocusEcho | null {
  const candidate = raw as {
    focus?: unknown;
    focus_version?: unknown;
    focus_refresh_required?: unknown;
  } | null;
  if (!candidate || typeof candidate !== 'object') return null;
  const refresh = candidate.focus_refresh_required;
  if (typeof refresh !== 'boolean') return null;
  const version = candidate.focus_version;
  if (typeof version !== 'number' || !Number.isInteger(version) || version < 0)
    return null;
  // `focus` is REQUIRED here too: a missing/undefined property never becomes
  // an implicit clear echo (only explicit `focus: null` does).
  if (candidate.focus === undefined) return null;
  const focus = candidate.focus ?? null;
  const normalizedFocus = normalizeFocus(focus);
  if (focus !== null && normalizedFocus === null) return null;
  return {
    focus: normalizedFocus,
    focus_version: version,
    focus_refresh_required: refresh
  };
}

/**
 * Strict refresh signal for looser envelopes (legacy STATE_DELTA payloads):
 * true ONLY for a boolean `focus_refresh_required === true`. A false, missing
 * or malformed flag can never move the chip.
 */
export function isFocusRefreshRequired(raw: unknown): boolean {
  return (
    (raw as { focus_refresh_required?: unknown } | null | undefined)
      ?.focus_refresh_required === true
  );
}

/**
 * Per-turn focus-refresh gate: at most ONE authoritative
 * `GET /threads/{id}/focus` refresh per logical turn, only for a normalized
 * valid `focus_refresh_required === true` signal from a turn that is still
 * live (not finished, not aborted, same thread, same selection generation).
 * Late, aborted and cross-thread events are ignored; the echoed snapshot is
 * never applied, so a false echo or a replayed stale version cannot move the
 * chip.
 */
export function createFocusRefreshGate(isTurnLive: () => boolean): {
  consume: (raw: unknown) => void;
  shouldRefresh: () => boolean;
} {
  let needed = false;
  return {
    consume(raw: unknown) {
      const echo = normalizeThreadFocusEcho(raw);
      const record = raw as
        | { focus?: unknown; focus_version?: unknown }
        | null
        | undefined;
      // A COMPLETE wire echo record (focus + focus_version present) claims to
      // be authoritative: it must survive strict normalization. A malformed
      // complete record is dropped outright — the loose legacy true-flag
      // reader never resurrects it. Only explicit legacy PARTIAL flag
      // envelopes (no record fields, e.g. STATE_DELTA payloads) stay loose.
      const claimsCompleteRecord =
        typeof record === 'object' &&
        record !== null &&
        record.focus !== undefined &&
        record.focus_version !== undefined;
      const refresh = echo
        ? echo.focus_refresh_required
        : !claimsCompleteRecord && isFocusRefreshRequired(raw);
      if (!refresh) return;
      if (!isTurnLive()) return;
      needed = true;
    },
    shouldRefresh() {
      return needed && isTurnLive();
    }
  };
}

/**
 * What the chip renders: a confirmed target, the unavailable/revoked state
 * (never a stale label), or a user-cleared "nothing" — three distinct truths.
 */
export type FocusChipState = 'target' | 'unavailable' | 'cleared';

export function focusChipState(
  focus: ConversationFocus | null | undefined,
  unavailable: boolean | undefined
): FocusChipState {
  if (focus) return 'target';
  return unavailable ? 'unavailable' : 'cleared';
}

export function focusRefOf(
  focus: ConversationFocus | null | undefined
): ConversationFocusRef | null {
  return focus ? { kind: focus.kind, id: focus.id } : null;
}

export function sameFocusRef(
  a: ConversationFocusRef | null | undefined,
  b: ConversationFocusRef | null | undefined
): boolean {
  return Boolean(a && b && a.kind === b.kind && a.id === b.id);
}

function csrfToken(): string {
  if (typeof document === 'undefined') return '';
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function focusUrl(host: string, path: string): string {
  return new URL(path, `${host.replace(/\/$/, '')}/`).toString();
}

export type ThreadFocusReadResult =
  | { ok: true; payload: ThreadFocusPayload }
  | { ok: false; code: 'not_found' | 'error' };

/**
 * Read one thread's conversation focus. A 404 means the thread has no server
 * row yet (client-minted ids materialize on the first owner write) — reported
 * as `not_found`, never fabricated as a confirmed empty state with a version.
 */
export async function fetchThreadFocus(
  threadId: string,
  host: string
): Promise<ThreadFocusReadResult> {
  try {
    const response = await fetch(
      focusUrl(host, `threads/${encodeURIComponent(threadId)}/focus`),
      {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include'
      }
    );
    if (response.status === 404) return { ok: false, code: 'not_found' };
    if (!response.ok) return { ok: false, code: 'error' };
    const payload = normalizeThreadFocusPayload(await response.json());
    if (!payload) return { ok: false, code: 'error' };
    return { ok: true, payload };
  } catch {
    return { ok: false, code: 'error' };
  }
}

export type ThreadFocusUpdateResult =
  | { ok: true; payload: ThreadFocusPayload }
  | { ok: false; code: FocusErrorCode | 'not_found' | 'error' };

/**
 * Replace (or clear) the thread's focus under optimistic concurrency. The
 * response is the exact server ack the entry preflight requires — a rejected
 * update leaves the server state unchanged and is surfaced verbatim in code
 * (including the safe `focus_out_of_analysis_scope` /
 * `focus_decision_not_set_aside` refusals). Nothing is auto-retried here.
 */
export async function updateThreadFocus(
  threadId: string,
  host: string,
  expectedFocusVersion: number,
  focus: ConversationFocusRef | null
): Promise<ThreadFocusUpdateResult> {
  try {
    const body: ThreadFocusUpdateRequest = {
      focus,
      expected_focus_version: expectedFocusVersion
    };
    const response = await fetch(
      focusUrl(host, `threads/${encodeURIComponent(threadId)}/focus`),
      {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken()
        },
        credentials: 'include',
        body: JSON.stringify(body)
      }
    );
    if (response.ok) {
      const payload = normalizeThreadFocusPayload(await response.json());
      if (!payload) return { ok: false, code: 'error' };
      return { ok: true, payload };
    }
    const detail = await response
      .json()
      .then((body) => (body as { detail?: unknown })?.detail)
      .catch(() => undefined);
    if (response.status === 409 && detail === 'focus_version_conflict') {
      return { ok: false, code: 'focus_version_conflict' };
    }
    // Exact typed 400 refusals propagate verbatim — the safe scope/set-aside
    // codes never collapse into the generic rejection.
    if (response.status === 400 && isFocusErrorCode(detail)) {
      return { ok: false, code: detail };
    }
    if (response.status === 404) return { ok: false, code: 'not_found' };
    // Malformed input or any other safe message: generic rejection.
    return { ok: false, code: 'focus_update_rejected' };
  } catch {
    return { ok: false, code: 'error' };
  }
}

export type FocusCandidateSearchResult =
  | { ok: true; payload: FocusCandidatesPayload }
  | { ok: false; code: string };

/**
 * Authorized candidate search for the picker. Search hints never authorize:
 * selecting a row still commits `{kind, id}` through the owner focus PUT.
 * `has_more` is the server's real truncation flag and the request limit is
 * bounded by the endpoint (1..25).
 */
export async function searchFocusCandidates(
  host: string,
  kind: ConversationFocusKind,
  query: string,
  limit = 10
): Promise<FocusCandidateSearchResult> {
  try {
    const params = new URLSearchParams({
      kind,
      query,
      limit: String(Math.max(1, Math.min(25, limit)))
    });
    const response = await fetch(
      focusUrl(host, `focus/candidates?${params.toString()}`),
      {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include'
      }
    );
    if (!response.ok) return { ok: false, code: `http_${response.status}` };
    const raw = (await response.json()) as {
      candidates?: unknown;
      returned_count?: unknown;
      has_more?: unknown;
    };
    if (!Array.isArray(raw.candidates)) return { ok: false, code: 'error' };
    const candidates = raw.candidates
      .map((entry) => normalizeFocus(entry))
      .filter((entry): entry is ConversationFocus => entry !== null);
    return {
      ok: true,
      payload: {
        candidates,
        returned_count:
          typeof raw.returned_count === 'number'
            ? raw.returned_count
            : candidates.length,
        // Truncation must never masquerade as a complete list: anything
        // truthy keeps the honest "more matches exist" warning.
        has_more: Boolean(raw.has_more)
      }
    };
  } catch {
    return { ok: false, code: 'error' };
  }
}
