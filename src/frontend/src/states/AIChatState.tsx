import type {
  ComponentFocusReference,
  EvidenceFocusReference
} from '@lib/types/AimmsWire.generated';
import { create } from 'zustand';
import { queryClient } from '../App';
import { clearChatIndices } from '../functions/chatThreadCache';

/**
 * A machine hint carried into the AI chat drawer (S14 B5, repurposed S2).
 *
 * The hint is the SCOPE SEED: the first send consumes it as a server-side
 * `PUT /threads/{id}/scope` (explicit single-machine analysis scope) —
 * never as message text. It grants nothing: the server re-authorizes the
 * machine id on the scope update and again on every turn, and a denied or
 * out-of-scope machine stays indistinguishable from a nonexistent one.
 * A missing capability or refused acknowledgement blocks the hinted send;
 * the user can retry or explicitly remove the machine selection.
 */
export interface AIChatRoutingHint {
  machineId: number;
  machineName: string;
}

/**
 * Plan C: the generalized pending conversation target ("talking about") a
 * page entry point hands to the drawer. `id` is the canonical PK for `kind`
 * (a `kanban_card` id is the CARD PK; its parent WorkOrder rides along for
 * display only). `label` is interim display text — the server resolves and
 * confirms the canonical label; the hint is a candidate, never a grant and
 * never a binding.
 */
export interface AIChatTargetHint {
  kind: 'machine' | 'kanban_card' | 'work_order';
  id: number;
  label: string;
  /** Parent machine id when the source page knows it (display only). */
  machineId?: number | null;
  /** Parent WorkOrder id for a board card (display only, distinct from id). */
  workOrderId?: number | null;
}

export function machineTargetHint(hint: AIChatRoutingHint): AIChatTargetHint {
  return {
    kind: 'machine',
    id: hint.machineId,
    label: hint.machineName,
    machineId: hint.machineId
  };
}

/**
 * P02/P33: a question asked from one Work order.
 *
 * Unlike the machine hint it never touches the analysis scope. Before the
 * first send the drawer asks the server to acknowledge this exact Work at this
 * exact `lifecycleVersion`; only that signed receipt goes with the message,
 * and a refusal, a missing capability or a malformed response sends nothing.
 * The label and path are display and navigation data, never authority.
 */
export interface AIChatWorkFocusHint {
  workOrderId: number | null;
  /** The Work's native `lifecycle_version` as the launching page showed it. */
  lifecycleVersion: number | null;
  component?: ComponentFocusReference;
  evidence?: EvidenceFocusReference;
  requestId?: number;
  requestVersion?: number;
  /** What the launching page showed, e.g. "WO-73 · Replace seal". */
  label: string;
  /** The launching route (path and query), validated before it is used. */
  returnPath: string;
}

function activeLauncher(): HTMLElement | null {
  if (typeof document === 'undefined' || typeof HTMLElement === 'undefined')
    return null;
  return document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
}

interface AIChatStateProps {
  isOpen: boolean;
  /** Ephemeral UI launcher; never serialized or sent as assistant context. */
  returnFocusTarget: HTMLElement | null;
  sessionGeneration: number;
  resetSession: (clearIndex?: boolean) => void;
  routingHint?: AIChatRoutingHint;
  /** Plan C: the generalized pending target (machine/task/work order). */
  targetHint?: AIChatTargetHint;
  /** P02/P33: at most one of `routingHint` and `workFocusHint` is set. */
  workFocusHint?: AIChatWorkFocusHint;
  hintThreadId: string | null;
  bindHint: (threadId: string) => void;
  /**
   * RV08: synchronously consume the pending hint — routing and target
   * together — at its acknowledging send boundary. The first acknowledging
   * thread consumes an unbound pending hint; a differently bound owner is
   * refused. Consumption is exactly once: nothing survives to re-apply on a
   * later send, another thread or another account.
   */
  consumeHint: (threadId: string) => void;
  open: () => void;
  openWithHint: (hint: AIChatRoutingHint) => void;
  /** Plan C: contextual Ask from a board task or work-order detail. */
  openWithTargetHint: (hint: AIChatTargetHint) => void;
  openWithWorkFocus: (
    hint: AIChatWorkFocusHint,
    target?: HTMLElement | null
  ) => void;
  close: () => void;
  clearHint: () => void;
  clearWorkFocus: () => void;
  advanceRequestFocus: (
    workOrderId: number,
    requestId: number,
    version: number
  ) => void;
  /**
   * Transient "open the Chat tab" intent set by machine Ask/scan. The drawer
   * consumes it exactly once (select Chat + focus the composer); it must not
   * keep forcing the tab after the user switches away.
   */
  chatOpenIntent: 'chat' | null;
  consumeChatOpenIntent: () => void;
}

export const useAIChatState = create<AIChatStateProps>()((set) => ({
  isOpen: false,
  returnFocusTarget: null,
  sessionGeneration: 0,
  resetSession: (clearIndex = true) => {
    if (clearIndex) clearChatIndices();
    // Drawer tabs share the application query cache. Remove their old account
    // data as well as the component state; a remount alone would reuse it.
    const privateQueries = new Set([
      'chat-action-proposals',
      'ai-evidence-set',
      'voice-capability',
      'ai-evidence-source',
      'management-metrics',
      'maintenance-metrics',
      'risk-scopes',
      'ui-capabilities',
      'voice-operation',
      'approval-inbox',
      'approval-review',
      'approval-count',
      'mailboxes',
      'mailbox-messages',
      'mailbox-review',
      'mailbox-operation'
    ]);
    queryClient.removeQueries({
      predicate: (query) => privateQueries.has(String(query.queryKey[0]))
    });
    set((state) => ({
      returnFocusTarget: null,
      sessionGeneration: state.sessionGeneration + 1,
      isOpen: false,
      routingHint: undefined,
      targetHint: undefined,
      workFocusHint: undefined,
      hintThreadId: null,
      chatOpenIntent: null
    }));
  },
  routingHint: undefined,
  targetHint: undefined,
  workFocusHint: undefined,
  hintThreadId: null,
  bindHint: (threadId) =>
    set((state) =>
      (state.routingHint || state.targetHint || state.workFocusHint) &&
      state.hintThreadId === null
        ? { hintThreadId: threadId }
        : {}
    ),
  consumeHint: (threadId) =>
    set((state) =>
      (state.routingHint || state.targetHint) &&
      (state.hintThreadId === null || state.hintThreadId === threadId)
        ? {
            routingHint: undefined,
            targetHint: undefined,
            // An investigation request can remain as a per-turn Work focus.
            hintThreadId: state.workFocusHint ? state.hintThreadId : null
          }
        : {}
    ),
  open: () =>
    set({
      isOpen: true,
      returnFocusTarget: activeLauncher()
    }),
  openWithHint: (hint: AIChatRoutingHint) =>
    set({
      isOpen: true,
      returnFocusTarget: activeLauncher(),
      routingHint: hint,
      targetHint: machineTargetHint(hint),
      workFocusHint: undefined,
      hintThreadId: null,
      chatOpenIntent: 'chat'
    }),
  openWithTargetHint: (hint: AIChatTargetHint) =>
    set({
      isOpen: true,
      returnFocusTarget: activeLauncher(),
      targetHint: hint,
      workFocusHint: undefined,
      // Only a machine target seeds the analysis scope.
      routingHint:
        hint.kind === 'machine'
          ? { machineId: hint.id, machineName: hint.label }
          : undefined,
      hintThreadId: null,
      chatOpenIntent: 'chat'
    }),
  openWithWorkFocus: (hint: AIChatWorkFocusHint, target?: HTMLElement | null) =>
    set({
      isOpen: true,
      returnFocusTarget: target ?? activeLauncher(),
      routingHint: undefined,
      targetHint: undefined,
      workFocusHint: hint,
      hintThreadId: null,
      chatOpenIntent: 'chat'
    }),
  close: () =>
    set({
      isOpen: false,
      returnFocusTarget: null,
      routingHint: undefined,
      targetHint: undefined,
      workFocusHint: undefined,
      hintThreadId: null,
      chatOpenIntent: null
    }),
  clearHint: () =>
    set({
      routingHint: undefined,
      targetHint: undefined,
      workFocusHint: undefined,
      hintThreadId: null
    }),
  clearWorkFocus: () =>
    set((state) => ({
      workFocusHint: undefined,
      hintThreadId:
        state.routingHint || state.targetHint ? state.hintThreadId : null
    })),
  advanceRequestFocus: (workOrderId, requestId, version) =>
    set((state) => {
      const hint = state.workFocusHint;
      if (
        !hint ||
        hint.workOrderId !== workOrderId ||
        hint.requestId !== requestId ||
        !Number.isSafeInteger(version) ||
        version <= (hint.requestVersion ?? 0)
      )
        return {};
      return { workFocusHint: { ...hint, requestVersion: version } };
    }),
  chatOpenIntent: null,
  consumeChatOpenIntent: () => set({ chatOpenIntent: null })
}));

/** P02/P33: open the one existing assistant about one Work order. */
export function openGlobalAIChatAboutWork(
  hint: AIChatWorkFocusHint,
  target?: HTMLElement
) {
  useAIChatState.getState().openWithWorkFocus(hint, target);
}

export function openGlobalAIChat(hint?: AIChatRoutingHint | AIChatTargetHint) {
  if (hint && 'kind' in hint) {
    useAIChatState.getState().openWithTargetHint(hint);
  } else if (hint) {
    useAIChatState.getState().openWithHint(hint);
  } else {
    useAIChatState.getState().open();
  }
}
