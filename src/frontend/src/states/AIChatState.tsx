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
 * P02/P33: a question asked from one Work order.
 *
 * Unlike the machine hint it never touches the analysis scope. Before the
 * first send the drawer asks the server to acknowledge this exact Work at this
 * exact `lifecycleVersion`; only that signed receipt goes with the message,
 * and a refusal, a missing capability or a malformed response sends nothing.
 * The label and path are display and navigation data, never authority.
 */
export interface AIChatWorkFocusHint {
  workOrderId: number;
  /** The Work's native `lifecycle_version` as the launching page showed it. */
  lifecycleVersion: number;
  requestId?: number;
  requestVersion?: number;
  /** What the launching page showed, e.g. "WO-73 · Replace seal". */
  label: string;
  /** The launching route (path and query), validated before it is used. */
  returnPath: string;
}

interface AIChatStateProps {
  isOpen: boolean;
  sessionGeneration: number;
  resetSession: (clearIndex?: boolean) => void;
  routingHint?: AIChatRoutingHint;
  /** P02/P33: at most one of `routingHint` and `workFocusHint` is set. */
  workFocusHint?: AIChatWorkFocusHint;
  hintThreadId: string | null;
  bindHint: (threadId: string) => void;
  open: () => void;
  openWithHint: (hint: AIChatRoutingHint) => void;
  openWithWorkFocus: (hint: AIChatWorkFocusHint) => void;
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
      sessionGeneration: state.sessionGeneration + 1,
      isOpen: false,
      routingHint: undefined,
      workFocusHint: undefined,
      hintThreadId: null,
      chatOpenIntent: null
    }));
  },
  routingHint: undefined,
  workFocusHint: undefined,
  hintThreadId: null,
  bindHint: (threadId) =>
    set((state) =>
      (state.routingHint || state.workFocusHint) && state.hintThreadId === null
        ? { hintThreadId: threadId }
        : {}
    ),
  open: () => set({ isOpen: true }),
  openWithHint: (hint: AIChatRoutingHint) =>
    set({
      isOpen: true,
      routingHint: hint,
      workFocusHint: undefined,
      hintThreadId: null,
      chatOpenIntent: 'chat'
    }),
  openWithWorkFocus: (hint: AIChatWorkFocusHint) =>
    set({
      isOpen: true,
      routingHint: undefined,
      workFocusHint: hint,
      hintThreadId: null,
      chatOpenIntent: 'chat'
    }),
  close: () =>
    set({
      isOpen: false,
      routingHint: undefined,
      workFocusHint: undefined,
      hintThreadId: null,
      chatOpenIntent: null
    }),
  clearHint: () => set({ routingHint: undefined, hintThreadId: null }),
  clearWorkFocus: () => set({ workFocusHint: undefined, hintThreadId: null }),
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
export function openGlobalAIChatAboutWork(hint: AIChatWorkFocusHint) {
  useAIChatState.getState().openWithWorkFocus(hint);
}

export function openGlobalAIChat(hint?: AIChatRoutingHint) {
  if (hint) {
    useAIChatState.getState().openWithHint(hint);
  } else {
    useAIChatState.getState().open();
  }
}
