import type { CaptureOriginal } from './Closeout';
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

export type HandledActionState =
  | 'completed'
  | 'active'
  | 'queued'
  | 'failed'
  | 'unknown';

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
  counts: Record<Exclude<HandledActionState, 'queued'>, number> & {
    queued?: number;
  };
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
  originals?: CaptureOriginal[];
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
  attachment_snapshot?: {
    id: number;
    filename: string | null;
    link: string | null;
    comment: string;
  } | null;
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
  updated_by_name?: string | null;
  created_by_name?: string | null;
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

export interface PackageStep {
  key: string;
  sequence: number;
  step_type: string;
  title: string;
  instruction: string;
  required: boolean;
  required_permission: string;
  value_type: string;
  unit: string;
  min_value: string | null;
  max_value: string | null;
  allowed_values: string[];
}

export interface PackageApplication {
  id: number;
  primary: boolean;
  procedure_id: number | null;
  revision_id: number | null;
  revision: number | null;
  change_summary: string | null;
  snapshot_hash: string;
  drift_status: string;
  /** Comparison made at this read, rather than the legacy stored default. */
  drift_basis?: 'catalogue_at_read';
  current_catalogue_code?: string | null;
  current_catalogue_name?: string | null;
  applied_by: string | null;
  applied_at: string;
  steps: PackageStep[];
  resources: {
    key: string;
    sequence: number;
    kind: string;
    part_id: number;
    /** Current catalogue label for authorized part readers, not snapshot content. */
    part_name?: string | null;
    quantity: string;
    fulfillment_mode: string;
    required: boolean;
    notes: string;
  }[];
}

export interface PackageScope {
  id: number;
  version: number;
  current: boolean;
  verified_cause: string;
  lines: { sequence: number; action: string }[];
  crew_size: number | null;
  planned_elapsed_minutes: number | null;
  approved_by: string | null;
  approved_at: string;
  note: string;
  superseded_at?: string | null;
  failure_codes?: string[];
}

export interface ScopeRequestSnapshot {
  version: number;
  purpose: string;
  next_check: string;
  status: InvestigationRequest['status'];
  owner_name: string | null;
  next_checkpoint: string | null;
  required_artifacts: InvestigationRequest['required_artifacts'];
  blocked_reason: string;
  source_finding?: number | null;
  source_proposal?: InvestigationRequest['source_proposal'];
  resolution?: string;
  priority?: number | null;
}

/** Native history is retained; the missing dependency graph stays explicit. */
export interface ScopeRevisionReview {
  rule: 'scope_revision.v1';
  state:
    | 'no_scope'
    | 'original'
    | 'revised'
    | 'no_current_scope'
    | 'conflicting_scope';
  current_scope_id: number | null;
  comparisons: {
    before_scope_id: number;
    after_scope_id: number;
    basis: 'retained_version_order';
  }[];
  dependency_impact: {
    kind: 'tasks' | 'materials' | 'resources' | 'permissions' | 'criteria';
    available: false;
    reason: string;
  }[];
  evidence_validity: { available: false; reason: 'no_scope_evidence_links' };
  request_revisions: {
    event_id: number;
    request_id: number;
    before: ScopeRequestSnapshot;
    after: ScopeRequestSnapshot;
    retained_evidence_ids: number[];
    recorded_by: string | null;
    recorded_at: string;
    scope_link: null;
    revision_reason: null;
  }[];
}

export interface PackageDeviation {
  id: number;
  category: string;
  application_key: string;
  step_key: string;
  resource_key: string;
  expected: unknown;
  actual: unknown;
  reason: string;
  recorded_by: string | null;
  created_at: string;
  resolution: string;
  resolved_at: string | null;
}

export interface PreparedPackage {
  rule: 'prepared_package.v1';
  scopes: PackageScope[];
  procedures_state: 'read' | 'not_enabled';
  applications: PackageApplication[];
  deviations: PackageDeviation[];
  can_record_deviation: boolean;
  physical_authority: { available: false; reason: 'no_provider' };
  affected_dependencies: { available: false; reason: 'no_provider' };
  revision_review?: ScopeRevisionReview;
}

/** Hold/resume record provenance is separate from site acceptance/permission. */
export interface WorkContinuity {
  rule: 'work_continuity.v1';
  lifecycle_version: number;
  current_hold: {
    reason: string | null;
    recorded_by: string | null;
    recorded_at: string | null;
    event_id: number | null;
  } | null;
  history: {
    event_id: number;
    action: 'hold' | 'resume';
    from_status: string;
    to_status: string;
    reason: string | null;
    recorded_by: string | null;
    recorded_at: string;
    lifecycle_version: number | null;
  }[];
  commands: {
    hold: boolean;
    resume: boolean;
    unavailable_reason:
      | 'api_disabled'
      | 'permission_required'
      | 'packet_owns_lifecycle'
      | null;
  };
  assignment_acceptance: {
    available: false;
    reason: 'no_site_acceptance_provider';
  };
  accepted_handover: { available: false; reason: 'no_provider' };
  physical_service_state: { available: false; reason: 'no_provider' };
  cross_crew_restrictions: { available: false; reason: 'no_provider' };
}

export interface VerificationTest {
  id: number;
  application_id: number;
  procedure_revision: number | null;
  step: PackageStep;
  version: number;
  status: string;
  value: unknown;
  passed: boolean | null;
  note: string;
  disposition_reason: string;
  recorded_by: string | null;
  recorded_at: string | null;
  events: {
    id: number;
    event_type: string;
    version: number | null;
    recorded_by: string | null;
    recorded_at: string;
    reason: string;
    recorded_note?: string | null;
    recorded_note_truncated?: boolean;
    evidence_ids: number[];
    historical_value_available: false;
  }[];
}

export interface VerificationReturn {
  rule: 'verification_return.v1';
  procedures_state: 'read' | 'not_enabled';
  tests: VerificationTest[];
  attempt_history:
    | 'outcomes_only_previous_values_not_retained'
    | 'events_and_notes_previous_values_not_retained';
  lockouts: {
    id: number;
    device: string;
    energy_source: string;
    status: string;
    applied_by: string | null;
    verified_by: string | null;
    verified_at: string | null;
    restored_at: string | null;
    note: string;
  }[];
  technical_acceptance: { available: false; reason: 'no_provider' };
  controlled_test_authority: { available: false; reason: 'no_provider' };
  operating_permission: { available: false; reason: 'no_provider' };
  observed_return: { available: false; reason: 'no_provider' };
  service_state: { available: false; reason: 'no_provider' };
}

export interface TimeAssignment {
  rule: 'time_assignment.v1';
  observed_at: string;
  assigned_to: string | null;
  legacy_assignee: string | null;
  assignment_event_at: string | null;
  elapsed_assignment_seconds: number | null;
  assignment_reason: string | null;
  assignment_recorded_by: string | null;
  events: {
    id: number;
    recorded_by: string | null;
    recorded_at: string;
    reason: string | null;
    assigned_to_id: number | null;
    previous_assigned_to_id: number | null;
    assigned_to_name?: string | null;
    previous_assigned_to_name?: string | null;
  }[];
  attributed_intervals: { available: false; reason: 'no_provider' };
  coverage: 'unknown';
  correction: { available: false; reason: 'no_provider' };
  assignment_factors: { available: false; reason: 'no_provider' };
  accepted_handover: { available: false; reason: 'no_provider' };
}
