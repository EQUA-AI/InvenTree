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
 * Against a backend without the scope capability it degrades to its
 * original role — a display-only chip.
 */
export interface AIChatRoutingHint {
  machineId: number;
  machineName: string;
}

interface AIChatStateProps {
  isOpen: boolean;
  sessionGeneration: number;
  resetSession: (clearIndex?: boolean) => void;
  routingHint?: AIChatRoutingHint;
  hintThreadId: string | null;
  bindHint: (threadId: string) => void;
  open: () => void;
  openWithHint: (hint: AIChatRoutingHint) => void;
  close: () => void;
  clearHint: () => void;
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
      hintThreadId: null
    }));
  },
  routingHint: undefined,
  hintThreadId: null,
  bindHint: (threadId) =>
    set((state) =>
      state.routingHint && state.hintThreadId === null
        ? { hintThreadId: threadId }
        : {}
    ),
  open: () => set({ isOpen: true }),
  openWithHint: (hint: AIChatRoutingHint) =>
    set({ isOpen: true, routingHint: hint, hintThreadId: null }),
  close: () =>
    set({ isOpen: false, routingHint: undefined, hintThreadId: null }),
  clearHint: () => set({ routingHint: undefined, hintThreadId: null })
}));

export function openGlobalAIChat(hint?: AIChatRoutingHint) {
  if (hint) {
    useAIChatState.getState().openWithHint(hint);
  } else {
    useAIChatState.getState().open();
  }
}
