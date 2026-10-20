/**
 * Shared context preflight decisions (plan packages A + C).
 *
 * Typed send and every voice entry path commit the same explicit target the
 * same way: as server-confirmed thread state before any turn or voice session
 * is created — never as message text and never dropped silently. A machine
 * hint is explicit user machine-context behavior and commits BOTH the
 * thread's explicit-assets analysis scope and the conversation focus; a
 * task/work-order target commits ONLY the conversation focus (a focus change
 * never widens an established analysis scope). These pure decisions keep both
 * paths identical:
 *
 * - machine scope reuse requires canonical machine IDS (fetch the full scope
 *   when a summary omitted them); a display label is never a binding —
 *   equal-name machines can never skip canonical confirmation;
 * - fail closed on a shared (read-only) thread, an in-flight application, a
 *   missing scope/focus capability, or a requested-hint error/conflict — an
 *   explicit selection that cannot be confirmed blocks the entry instead of
 *   continuing as a display-only chip with an unscoped turn;
 * - the focus PUT runs LAST so its exact server response is the ack the
 *   entry submits against (no guessed or label-only confirmed match).
 *
 * Authorization stays server-side: a hint is a candidate, never a grant.
 */
import type { ConversationFocusRef } from './conversationFocus';

/**
 * The pending explicit target. `id` is the canonical PK for `kind` (a
 * `kanban_card` id is the CARD PK — never its parent WorkOrder). `label` is
 * interim display text only and never participates in any match.
 */
export interface ContextTargetHint {
  kind: 'machine' | 'kanban_card' | 'work_order';
  id: number;
  label: string;
}

/** Confirmed thread scope as the preflight consumes it. */
export interface ConfirmedContextScope {
  mode: string;
  displayLabel?: string | null;
  machineIds?: number[] | null;
  machineCount?: number | null;
}

/** An explicit-assets scope update, as the thread scope PUT expects it. */
export interface ExplicitMachineScope {
  mode: 'explicit_assets';
  machine_ids: number[];
  display_label: string;
}

export type ContextPreflightPlan =
  | {
      action: 'proceed';
      reason: 'no-hint';
    }
  | {
      action: 'apply';
      /** Present only for machine hints whose confirmed scope differs. */
      scope?: ExplicitMachineScope;
      focus: ConversationFocusRef;
    }
  /** Machine hint whose scope summary omitted canonical ids: fetch first. */
  | { action: 'resolve-scope' }
  | {
      action: 'blocked';
      code:
        | 'shared-thread'
        | 'in-flight'
        | 'focus-unsupported'
        | 'scope-unsupported';
    };

/**
 * Result of awaiting the shared context preflight. `threadId` is the
 * authoritative thread for session creation; `focusVersion` is the exact
 * server-acknowledged version the entry stamps on its turn. `ok: false`
 * blocks the typed send or the voice start (fail closed).
 */
export type ContextPreflightOutcome =
  | { ok: true; threadId: string | null; focusVersion?: number | null }
  | { ok: false; code: string };

export type ContextPreflight = (context: {
  threadId: string | null;
  /** Which entry is being admitted — for honest failure feedback only. */
  entry?: 'typed' | 'voice';
}) => Promise<ContextPreflightOutcome>;

export interface ContextPreflightDeps {
  hint: ContextTargetHint | null | undefined;
  /** The backend advertises the thread-scope endpoints. */
  scopeCapable: boolean;
  /** The backend advertises the thread-focus endpoints. */
  focusCapable: boolean;
  threadShared: boolean;
  /** Another scope application is already in flight. */
  busy?: boolean;
  confirmed: ConfirmedContextScope | null | undefined;
  /** Current (authoritative) thread id for session creation. */
  threadId: string | null;
  /** Commits an explicit-assets scope on the thread. */
  applyScope: (
    scope: ExplicitMachineScope
  ) => Promise<{ ok: boolean; code?: string }>;
  /** Fetches the full scope payload when summary-derived state lacks ids. */
  fetchScope?: () => Promise<ConfirmedContextScope | null>;
  /**
   * Commits the target on the owned thread and returns the exact server ack
   * (`version` = confirmed `focus_version`). The caller applies nothing that
   * the server did not echo.
   */
  applyFocus: (
    ref: ConversationFocusRef
  ) => Promise<{ ok: boolean; code?: string; version?: number | null }>;
  /** Re-checked after every await: false when the context changed. */
  contextUnchanged: () => boolean;
}

/**
 * The shared preflight itself: plans the target commit, awaits it, and
 * returns the authoritative thread for session creation plus the focus
 * version to stamp. Typed send and every voice entry path await this exact
 * function — it never touches the typed draft or attachments, reuses a
 * confirmed matching machine scope without a redundant PUT, and fails closed
 * on requested-hint errors, conflicts, shared threads, in-flight work,
 * unconfirmable scope/focus, or a changed context.
 */
export async function runContextPreflight(
  deps: ContextPreflightDeps
): Promise<ContextPreflightOutcome> {
  let plan = planContextPreflight(deps);
  if (plan.action === 'resolve-scope') {
    // Never decide from a display label: resolve the canonical ids first.
    const fresh = deps.fetchScope ? await deps.fetchScope() : null;
    if (!deps.contextUnchanged()) return { ok: false, code: 'context_changed' };
    plan = planContextPreflight({ ...deps, confirmed: fresh });
    if (plan.action === 'resolve-scope' && deps.hint) {
      // Still no canonical ids after the fetch. Reuse is then impossible —
      // the only safe alternative to a label match is an explicit scope PUT
      // (the server re-authorizes the machine id and echoes the canonical
      // scope back).
      plan = {
        action: 'apply',
        scope: explicitMachineScope(deps.hint),
        focus: { kind: deps.hint.kind, id: deps.hint.id }
      };
    }
  }
  if (plan.action === 'resolve-scope') {
    // Unreachable in practice (resolve-scope implies a hint); keep the
    // fail-closed shape for defensive callers.
    return { ok: false, code: 'scope_unconfirmed' };
  }
  if (plan.action === 'blocked') return { ok: false, code: plan.code };
  if (plan.action === 'proceed') return { ok: true, threadId: deps.threadId };
  if (plan.scope) {
    const result = await deps.applyScope(plan.scope);
    if (!deps.contextUnchanged()) return { ok: false, code: 'context_changed' };
    if (!result.ok)
      return { ok: false, code: result.code ?? 'scope_update_rejected' };
  }
  // Last: the exact focus ack binds the entry. A denied or conflicting
  // candidate BLOCKS — never a display-only chip followed by a turn.
  const focus = await deps.applyFocus(plan.focus);
  if (!deps.contextUnchanged()) return { ok: false, code: 'context_changed' };
  if (!focus.ok)
    return { ok: false, code: focus.code ?? 'focus_update_rejected' };
  return {
    ok: true,
    threadId: deps.threadId,
    focusVersion: focus.version ?? null
  };
}

/**
 * True when the server-confirmed scope already covers the hint's machine.
 * Machine IDS are the only binding: a summary-derived scope without ids is
 * NOT covered (the preflight fetches the full scope instead), and a display
 * label never matches — two machines may share a name. This is a reuse
 * decision only; authorization is and stays server-side.
 */
export function machineScopeCoversHint(
  confirmed: ConfirmedContextScope | null | undefined,
  hint: ContextTargetHint | null | undefined
): boolean {
  if (!hint || hint.kind !== 'machine') return false;
  if (!confirmed) return false;
  if (confirmed.mode !== 'explicit_assets') return false;
  if (confirmed.machineIds == null) return false;
  return (
    confirmed.machineIds.length === 1 && confirmed.machineIds[0] === hint.id
  );
}

/** The explicit-assets scope seeded by an explicit machine-context hint. */
export function explicitMachineScope(
  hint: ContextTargetHint
): ExplicitMachineScope {
  return {
    mode: 'explicit_assets',
    machine_ids: [hint.id],
    display_label: hint.label.slice(0, 120)
  };
}

export function planContextPreflight(input: {
  hint: ContextTargetHint | null | undefined;
  scopeCapable: boolean;
  focusCapable: boolean;
  threadShared: boolean;
  /** Another scope application is already in flight. */
  busy?: boolean;
  confirmed: ConfirmedContextScope | null | undefined;
}): ContextPreflightPlan {
  if (!input.hint) return { action: 'proceed', reason: 'no-hint' };
  if (input.threadShared) return { action: 'blocked', code: 'shared-thread' };
  if (input.busy) return { action: 'blocked', code: 'in-flight' };
  // An explicit selection must be confirmable: without focus support the
  // candidate can never be acknowledged, and without scope support a machine
  // selection can never commit its explicit machine context. Both BLOCK —
  // the old "display-only chip and proceed" degrade is explicitly rejected.
  if (!input.focusCapable)
    return { action: 'blocked', code: 'focus-unsupported' };
  const focus: ConversationFocusRef = {
    kind: input.hint.kind,
    id: input.hint.id
  };
  if (input.hint.kind !== 'machine') {
    // Task/work-order targets never touch analysis scope.
    return { action: 'apply', focus };
  }
  if (!input.scopeCapable)
    return { action: 'blocked', code: 'scope-unsupported' };
  if (input.confirmed && input.confirmed.machineIds == null) {
    // Ids never observed (summary-derived scope): fetch the full scope
    // before any reuse decision — labels are not bindings.
    return { action: 'resolve-scope' };
  }
  if (machineScopeCoversHint(input.confirmed, input.hint)) {
    return { action: 'apply', focus };
  }
  return {
    action: 'apply',
    scope: explicitMachineScope(input.hint),
    focus
  };
}

/**
 * Outcome when no drawer-owned preflight is registered (surface unmounted):
 * an unconsumed target hint must never be silently dropped into an unscoped
 * entry; without a pending target there is nothing to commit and the
 * ordinary legacy unscoped interaction proceeds.
 */
export function unregisteredPreflightOutcome(input: {
  hintPending: boolean;
  threadId: string | null;
}): ContextPreflightOutcome {
  if (input.hintPending) return { ok: false, code: 'context_unconfirmed' };
  return { ok: true, threadId: input.threadId };
}

export interface SessionTurnContext {
  turnSessionId: string | null | undefined;
  turnThreadId: string | null | undefined;
  currentSessionId: string | null | undefined;
  /** Visible thread when the current session was created (null = unbound). */
  createdThreadId: string | null | undefined;
  visibleThreadId: string | null | undefined;
}

/**
 * True when a turn result belongs to the current voice session AND the
 * visible thread has not moved away since that session was created. A late
 * result from an old session (or one whose thread context was switched away
 * before the controller teardown landed) may stay in its thread's history
 * but must never drive drawer callbacks.
 */
export function isFreshSessionTurn(input: SessionTurnContext): boolean {
  if (!input.currentSessionId) return false;
  if (input.currentSessionId !== input.turnSessionId) return false;
  if (input.createdThreadId != null) {
    // A bound session is fresh only while the drawer still shows its
    // creation thread; a deliberate switch away is never navigated back.
    return input.createdThreadId === input.visibleThreadId;
  }
  // RV15: an unbound session (created with no visible thread) is fresh only
  // while nothing else is selected (the initial undefined→server-created
  // adoption) or the visible thread is already the result's own thread. A
  // visible thread that differs is a deliberate selection change made after
  // creation — a late result must never navigate it anywhere or re-arm its
  // question/focus callbacks.
  return (
    input.visibleThreadId == null ||
    input.visibleThreadId === input.turnThreadId
  );
}

/**
 * True when the fresh turn's server-assigned thread may become the drawer's
 * visible thread (initial adoption). A deliberate switch away from the
 * session's creation thread is never navigated back by a late result.
 */
export function canAdoptSessionThread(input: SessionTurnContext): boolean {
  return (
    isFreshSessionTurn(input) &&
    input.turnThreadId != null &&
    input.turnThreadId !== input.visibleThreadId
  );
}
