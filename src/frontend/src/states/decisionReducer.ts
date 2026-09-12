import type {
  VoiceDecisionContext,
  VoiceDecisionEvent,
  VoicePendingDecision
} from '../../lib/types/Voice';

export interface DecisionSnapshot {
  pending_decision: VoicePendingDecision | null;
  decision_event?: VoiceDecisionEvent | null;
}
export interface DecisionState {
  sessionId: string | null;
  decision: VoicePendingDecision | null;
  event: VoiceDecisionEvent | null;
  retiredIds: string[];
}
export const emptyDecisionState: DecisionState = {
  sessionId: null,
  decision: null,
  event: null,
  retiredIds: []
};

export function decisionContext(
  decision: VoicePendingDecision | null
): VoiceDecisionContext | null {
  return decision
    ? {
        decision_id: decision.decision_id,
        sequence: decision.sequence,
        revision: decision.revision,
        preview_hash: decision.preview_hash
      }
    : null;
}

/** Stale responses cannot roll a focus backward or resurrect a replaced target. */
export function decisionReducer(
  state: DecisionState,
  sessionId: string,
  snapshot: DecisionSnapshot
): DecisionState {
  if (state.sessionId !== sessionId) return state;
  const next = snapshot.pending_decision;
  if (!next) return state;
  if (state.retiredIds.includes(next.decision_id)) return state;
  if (
    state.decision?.decision_id === next.decision_id &&
    state.decision.sequence > next.sequence
  )
    return state;
  const retiredIds =
    state.decision && state.decision.decision_id !== next.decision_id
      ? [...state.retiredIds, state.decision.decision_id].slice(-100)
      : state.retiredIds;
  return {
    ...state,
    decision: next,
    event: snapshot.decision_event ?? state.event,
    retiredIds
  };
}
