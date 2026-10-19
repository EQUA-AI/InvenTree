/**
 * R6 S4 Work execution projections carried by the work-order overview.
 *
 * Mirrors `tasks/work_execution/*.py`. Every field is optional on the overview:
 * a server without the projection simply omits it, and the Work page then
 * says the fact is not supplied rather than showing an empty success.
 */

/** Which record families a projection read, so empty is never "none". */
export interface ProjectionSource {
  key: string;
  state: 'read' | 'not_applicable' | 'not_enabled';
}

export type HandledActionState = 'completed' | 'active' | 'failed' | 'unknown';

export type HandledActionKind =
  | 'packet_prepared'
  | 'work_order_prepared'
  | 'account_drafted'
  | 'account_drafting'
  | 'closeout_notification'
  | 'learning_drafted'
  | 'closeout_follow_up';

export type HandledActionLink =
  | { kind: 'repair_packet'; id: number; reference: string }
  | { kind: 'work_panel'; panel: string };

/** One recorded digital action (`handled_actions.v1`). */
export interface HandledAction {
  id: string;
  kind: HandledActionKind;
  state: HandledActionState;
  /** The source record's own status value, e.g. `succeeded` or `retryable`. */
  status: string;
  occurred_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  source: { record: string; id: number; provider: string | null };
  trigger: Record<string, string | null> | null;
  result: Record<string, number | string | boolean | null> | null;
  link: HandledActionLink | null;
  attempts: number | null;
}

export interface HandledActions {
  rule: 'handled_actions.v1';
  sources: ProjectionSource[];
  counts: Record<HandledActionState, number>;
  actions: HandledAction[];
}
