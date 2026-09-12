import { create } from 'zustand';
import { api } from '../App';
import {
  type DecisionSnapshot,
  type DecisionState,
  decisionContext,
  decisionReducer,
  emptyDecisionState
} from './decisionReducer';

interface VoiceDecisionStore extends DecisionState {
  setSession: (sessionId: string | null) => void;
  applyTurn: (sessionId: string, snapshot: DecisionSnapshot) => void;
  refresh: () => Promise<void>;
  decide: (
    action: string,
    phrase?: string,
    playback?: { utterance_id: string; spoken_summary_hash: string }
  ) => Promise<void>;
}

export const useVoiceDecisionState = create<VoiceDecisionStore>((set, get) => ({
  ...emptyDecisionState,
  setSession: (sessionId) => set({ ...emptyDecisionState, sessionId }),
  applyTurn: (sessionId, snapshot) =>
    set((state) => decisionReducer(state, sessionId, snapshot)),
  refresh: async () => {
    const { sessionId, decision: before } = get();
    if (!sessionId) return;
    const response = await api.get(
      `/api/ai/voice/sessions/${sessionId}/decision`
    );
    if (!response.data.pending_decision) {
      // A null read has no server sequence. Only clear the exact snapshot
      // this request started with; a newer turn must win a polling race.
      set((state) =>
        state.sessionId === sessionId && state.decision === before
          ? {
              decision: null,
              event: response.data.decision_event ?? null,
              retiredIds: before
                ? [...state.retiredIds, before.decision_id].slice(-100)
                : state.retiredIds
            }
          : state
      );
    } else {
      get().applyTurn(sessionId, response.data);
    }
  },
  decide: async (action, phrase, playback) => {
    const { sessionId, decision } = get();
    const context = decisionContext(decision);
    if (!sessionId || !context) throw new Error('No decision is active');
    try {
      const response = await api.post(
        `/api/ai/voice/sessions/${sessionId}/decision/${action}`,
        {
          ...context,
          confirm_phrase: phrase ?? '',
          ...playback
        }
      );
      get().applyTurn(sessionId, response.data);
    } finally {
      window.dispatchEvent(new Event('aimms:proposals-refresh'));
    }
  }
}));
