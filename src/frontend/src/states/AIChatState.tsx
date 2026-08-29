import { create } from 'zustand';

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
  routingHint?: AIChatRoutingHint;
  open: () => void;
  openWithHint: (hint: AIChatRoutingHint) => void;
  close: () => void;
  clearHint: () => void;
}

export const useAIChatState = create<AIChatStateProps>()((set) => ({
  isOpen: false,
  routingHint: undefined,
  open: () => set({ isOpen: true }),
  openWithHint: (hint: AIChatRoutingHint) =>
    set({ isOpen: true, routingHint: hint }),
  close: () => set({ isOpen: false }),
  clearHint: () => set({ routingHint: undefined })
}));

export function openGlobalAIChat(hint?: AIChatRoutingHint) {
  if (hint) {
    useAIChatState.getState().openWithHint(hint);
  } else {
    useAIChatState.getState().open();
  }
}
