/**
 * Thread conversation-focus state (plan package C).
 *
 * One global owner of the CURRENT thread's server-confirmed focus. The chat
 * hook binds it on thread switches and consumes its version for turn stamps;
 * the Talking about chip (drawer + hands-free surface) renders and mutates it.
 *
 * Rules enforced here:
 * - state is SERVER-derived only (GET/PUT acks); a UI label is never a
 *   binding and a turn's focus echo is never applied (it is that turn's
 *   recorded snapshot, not current thread state);
 * - thread isolation: an ack or read that resolves after a thread switch is
 *   dropped, so two threads keep independent focus;
 * - read-only shared threads can read but never select/mutate;
 * - writes quote `expected_focus_version` (null = never observed → 0) and a
 *   rejected write leaves the visible state untouched (the server state is
 *   unchanged on error by contract).
 */
import { create } from 'zustand';

import {
  type ConversationFocus,
  type ConversationFocusRef,
  type ThreadFocusPayload,
  fetchThreadFocus,
  updateThreadFocus
} from '../components/aichat/conversationFocus';

export type ThreadFocusStatus =
  | 'idle'
  | 'loading'
  | 'ready'
  | 'updating'
  | 'error';

export interface ThreadFocusBinding {
  threadId: string;
  /** S32b read-only thread: focus is visible but never selectable. */
  shared: boolean;
  host: string;
}

interface ThreadFocusStateProps {
  binding: ThreadFocusBinding | null;
  status: ThreadFocusStatus;
  focus: ConversationFocus | null;
  /**
   * The stored target no longer re-authorizes for THIS reader (revoked,
   * deleted or access lost). Deliberately distinct from a user-cleared focus
   * (`focus: null`, `unavailable: false`) — the two must never collapse.
   */
  unavailable: boolean;
  /** Observed server version; null = never observed (no stamp is invented). */
  version: number | null;
  error: string | null;
  bind: (input: ThreadFocusBinding) => void;
  unbind: () => void;
  refresh: () => Promise<void>;
  /** Apply an exact server ack (GET/PUT response) for a specific thread. */
  applyAck: (payload: ThreadFocusPayload, threadId?: string) => void;
  select: (
    ref: ConversationFocusRef | null,
    onAcknowledged?: (version: number) => void
  ) => Promise<{ ok: boolean; code?: string }>;
}

export const useThreadFocusState = create<ThreadFocusStateProps>()(
  (set, get) => {
    // Monotonic binding generation: every bind invalidates in-flight reads and
    // writes for the previous thread (thread focus isolation).
    let generation = 0;
    // Real client-side request ordering: `requestSeq` numbers every read/write
    // at INVOCATION time; `appliedSeq` is the newest request whose result has
    // been applied (or acknowledged) to the visible state. A result older than
    // `appliedSeq` can never roll back a newer observation.
    let requestSeq = 0;
    let appliedSeq = 0;
    const current = () => get().binding;
    return {
      binding: null,
      status: 'idle',
      focus: null,
      unavailable: false,
      version: null,
      error: null,
      bind: (input) => {
        const previous = current();
        if (
          previous &&
          previous.threadId === input.threadId &&
          previous.shared === input.shared &&
          previous.host === input.host
        ) {
          return;
        }
        generation += 1;
        set({
          binding: input,
          status: 'idle',
          focus: null,
          unavailable: false,
          version: null,
          error: null
        });
      },
      unbind: () => {
        generation += 1;
        set({
          binding: null,
          status: 'idle',
          focus: null,
          unavailable: false,
          version: null,
          error: null
        });
      },
      refresh: async () => {
        const binding = current();
        if (!binding) return;
        const run = generation;
        const seq = ++requestSeq;
        set({ status: 'loading', error: null });
        const result = await fetchThreadFocus(binding.threadId, binding.host);
        if (run !== generation) return;
        // Stale same-thread result: real read ordering / write acknowledgement
        // is fenced by request sequence. A read issued BEFORE a newer
        // observation was applied (an owner PUT ack, or a later read) can never
        // roll it back — that covers the delayed pre-write 404 (captured before
        // the PUT that then materialized the row) and reordered equal-version
        // reads (the backend re-authorizes every read as the current actor and
        // may return `focus_unavailable` at the SAME stored `focus_version`).
        // Additionally a strictly older `focus_version` is provably stale
        // (versions are monotonic per thread). A genuinely fresh read — issued
        // with nothing newer applied since — is authoritative even on 404 and
        // reaches the no-row/denial handling below; a confirmed label is never
        // retained indefinitely against an authoritative 404.
        const observed = get().version;
        const stale =
          seq < appliedSeq ||
          (result.ok &&
            observed !== null &&
            result.payload.focus_version < observed);
        if (stale) {
          // A dropped read is never left trapped in 'loading'.
          if (get().status === 'loading') {
            set({ status: 'ready', error: null });
          }
          return;
        }
        if (!result.ok) {
          if (result.code === 'not_found') {
            // No server row (client-minted thread), or a thread-level access
            // denial (the backend re-authorizes every read): no focus and no
            // version. The owner focus PUT materializes the thread on first
            // commit.
            appliedSeq = Math.max(appliedSeq, seq);
            set({
              status: 'ready',
              focus: null,
              unavailable: false,
              version: null,
              error: null
            });
            return;
          }
          set({ status: 'error', error: result.code });
          return;
        }
        appliedSeq = Math.max(appliedSeq, seq);
        set({
          status: 'ready',
          focus: result.payload.focus,
          unavailable: result.payload.focus_unavailable,
          version: result.payload.focus_version,
          error: null
        });
      },
      applyAck: (payload, threadId) => {
        const binding = current();
        if (!binding) return;
        if (threadId !== undefined && binding.threadId !== threadId) return;
        // R05 sibling acknowledgement path: a strictly older focus_version is
        // provably stale (versions are monotonic per thread), so a late routed
        // old ack can never roll the visible state back. Equal versions still
        // apply (the backend may flip focus_unavailable at the same version).
        const observed = get().version;
        if (observed !== null && payload.focus_version < observed) return;
        // An exact server ack is a newer authoritative observation: it fences
        // any read issued before it (e.g. a delayed pre-write 404).
        appliedSeq = Math.max(appliedSeq, ++requestSeq);
        set({
          status: 'ready',
          focus: payload.focus,
          unavailable: payload.focus_unavailable,
          version: payload.focus_version,
          error: null
        });
      },
      select: async (ref, onAcknowledged) => {
        const binding = current();
        if (!binding) return { ok: false, code: 'no-thread' };
        if (binding.shared) return { ok: false, code: 'shared-thread' };
        const run = generation;
        const seq = ++requestSeq;
        set({ status: 'updating', error: null });
        const result = await updateThreadFocus(
          binding.threadId,
          binding.host,
          get().version ?? 0,
          ref
        );
        if (run !== generation) return { ok: false, code: 'context_changed' };
        // R05 write-ack ordering: a result older than the newest applied
        // observation — a delayed old PUT acknowledgement resolving after a
        // newer write ack or read was applied — can never clear or roll back
        // newer state: success, refusal or conflict alike. The caller still
        // receives the real outcome; FRESH results behave exactly as before
        // (acks apply, 409 converges, refusals surface their code).
        const fresh = seq >= appliedSeq;
        if (result.ok) {
          if (fresh) {
            // A newer acknowledged selection applies and fences older
            // outstanding reads (the delayed pre-write 404 control).
            appliedSeq = Math.max(appliedSeq, seq);
            set({
              status: 'ready',
              focus: result.payload.focus,
              unavailable: result.payload.focus_unavailable,
              version: result.payload.focus_version,
              error: null
            });
          }
          // RV09: publish this entry's own ack without reading global state.
          onAcknowledged?.(result.payload.focus_version);
          return { ok: true };
        }
        if (result.code === 'focus_version_conflict') {
          if (fresh) {
            // The server state moved under us: surface the code and converge the
            // visible state from the server (never adopt the rejected write).
            set({ status: 'error', error: result.code });
            await get().refresh();
          }
          return { ok: false, code: result.code };
        }
        // Typed refusals (focus_update_rejected, focus_out_of_analysis_scope,
        // focus_decision_not_set_aside, …): the server state is unchanged, so
        // the visible state stays untouched and NOTHING is auto-retried — a
        // failed set-aside never consumes an approval, and an out-of-scope
        // refusal never widens focus or scope.
        if (fresh) {
          set({ status: 'ready', error: result.code });
        }
        return { ok: false, code: result.code };
      }
    };
  }
);
