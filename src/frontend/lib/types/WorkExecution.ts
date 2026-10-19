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

export type EvidenceRelation = 'supports' | 'contradicts' | 'unknown';

export interface InvestigationEvidence {
  observation: string;
  relation: EvidenceRelation;
  snapshot_id: string | null;
  signal_label?: string | null;
  value?: number | string | null;
  unit?: string | null;
  observed_at?: string | null;
  stale?: boolean | null;
}

export interface InvestigationAmendment {
  text: string;
  author: string | null;
  recorded_at: string | null;
  reason: string | null;
}

/** The saved explanation, without the model's confidence number. */
export interface InvestigationExplanation {
  likely_cause: string;
  failure_mode: string | null;
  /** True until a person verifies it (`repair.schema.is_preliminary`). */
  preliminary: boolean;
  verified_by: string | null;
  verified_at: string | null;
  analysis_status: string | null;
  authority: string | null;
  provider: string | null;
  model_or_rule_version: string | null;
  generated_at: string | null;
  data_window: {
    start: string | null;
    end: string | null;
    snapshot_count: number | null;
  };
  stale_signal_count: number | null;
  quality_summary: string | null;
  evidence: InvestigationEvidence[];
  alternatives: string[];
  /** Checks the diagnosis proposes; not saved requests with an owner. */
  proposed_checks: string[];
  amendments: InvestigationAmendment[];
}

export interface InvestigationFinding {
  id: number;
  finding_key: string;
  category: string;
  observation: string;
  value: number | null;
  unit: string;
  evidence_source: string;
  snapshot_id: string | null;
  observed_at: string | null;
  verification: string;
  recorded_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface InvestigationIntervention {
  scope_id: number;
  version: number;
  verified_cause: string;
  failure_codes: string[];
  scope_line_count: number;
  approved_by: string | null;
  approved_at: string;
  superseded_versions: number;
}

/** `investigation.v1`: what Diagnose projects from the saved investigation. */
export interface Investigation {
  rule: 'investigation.v1';
  packet: { id: number; reference: string; generation_status: string } | null;
  explanation: InvestigationExplanation | null;
  findings: InvestigationFinding[];
  finding_history:
    | 'overwritten_in_place'
    | 'not_applicable'
    | 'preserved_revisions';
  finding_revisions?: FindingRevision[];
  intervention: InvestigationIntervention | null;
  requests: InvestigationRequests;
}

export interface InvestigationArtifact {
  id: number;
  request: number;
  kind:
    | 'observation'
    | 'reading_set'
    | 'photo'
    | 'report'
    | 'laboratory'
    | 'check_result';
  payload: Record<string, unknown>;
  attachment: number | null;
  supersedes: number | null;
  recorded_by: number;
  recorded_by_name: string | null;
  /** Audit ids grant no access to the private source conversation. */
  source_turn_id?: string;
  source_proposal_id?: string | null;
  source_file?: {
    file_id: string;
    filename: string;
    size: number;
    sha256: string;
    is_image: boolean;
    supported: boolean;
  } | null;
  created_at: string;
}
export interface InvestigationRequest {
  id: number;
  work_order: number;
  schema_version: 1;
  version: number;
  purpose: string;
  next_check: string;
  priority: number | null;
  required_artifacts: {
    kind: InvestigationArtifact['kind'];
    description: string;
    unit?: string;
    conditions?: string;
  }[];
  status:
    | 'open'
    | 'answered'
    | 'cannot_perform'
    | 'pending_external'
    | 'disputed'
    | 'resolved';
  owner: number | null;
  owner_name: string | null;
  next_checkpoint: string | null;
  source_finding: number | null;
  source_proposal: {
    packet_id: number;
    check: string;
    generated_at: string | null;
  } | null;
  blocked_reason: string;
  resolution: string;
  created_by: number;
  updated_by: number;
  created_at: string;
  updated_at: string;
  artifacts: InvestigationArtifact[];
}
export interface InvestigationRequests {
  available: boolean;
  reason: string;
  items?: InvestigationRequest[];
}
export interface FindingRevision {
  id: number;
  finding_id: number;
  snapshot: InvestigationFinding;
  replaced_by: string | null;
  replaced_at: string;
}
