import { create } from 'zustand';

/**
 * A visible routing hint carried into the AI chat drawer (S14 B5).
 *
 * The hint is display text only: it pre-fills context so the assistant knows
 * which machine the user is asking about, but every tool call re-authorizes
 * server-side under the acting user. A denied or out-of-scope machine stays
 * indistinguishable from a nonexistent one regardless of what the hint claims.
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
