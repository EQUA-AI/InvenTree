// GENERATED FILE — DO NOT EDIT.
//
// Source of truth: backend definitions (see
// aichat/management/commands/generate_wire_contract.py). Regenerate with:
//     python manage.py generate_wire_contract
// CI runs `generate_wire_contract --check` and fails on drift.

// --- AG-UI event types (ai.core.streaming.EventType) ---

export enum AGUIEventType {
  RUN_STARTED = 'RUN_STARTED',
  RUN_FINISHED = 'RUN_FINISHED',
  RUN_ERROR = 'RUN_ERROR',
  RUN_CANCELLED = 'RUN_CANCELLED',
  AGENT_THINKING = 'AGENT_THINKING',
  AGENT_EXECUTING = 'AGENT_EXECUTING',
  AGENT_WAITING = 'AGENT_WAITING',
  AGENT_HANDOFF = 'AGENT_HANDOFF',
  TEXT_MESSAGE_START = 'TEXT_MESSAGE_START',
  TEXT_MESSAGE_CONTENT = 'TEXT_MESSAGE_CONTENT',
  TEXT_MESSAGE_END = 'TEXT_MESSAGE_END',
  TEXT_MESSAGE_CHUNK = 'TEXT_MESSAGE_CHUNK',
  TOOL_CALL_START = 'TOOL_CALL_START',
  TOOL_CALL_ARGS = 'TOOL_CALL_ARGS',
  TOOL_CALL_END = 'TOOL_CALL_END',
  TOOL_CALL_RESULT = 'TOOL_CALL_RESULT',
  QUESTION = 'QUESTION',
  HITL_REQUIRED = 'HITL_REQUIRED',
  HITL_APPROVED = 'HITL_APPROVED',
  HITL_REJECTED = 'HITL_REJECTED',
  HITL_TIMEOUT = 'HITL_TIMEOUT',
  PROGRESS_UPDATE = 'PROGRESS_UPDATE',
  STEP_STARTED = 'STEP_STARTED',
  STEP_FINISHED = 'STEP_FINISHED',
  WORKFLOW_STARTED = 'WORKFLOW_STARTED',
  WORKFLOW_COMPLETED = 'WORKFLOW_COMPLETED',
  WORKFLOW_STEP = 'WORKFLOW_STEP',
  STATE_SNAPSHOT = 'STATE_SNAPSHOT',
  STATE_DELTA = 'STATE_DELTA',
  MESSAGES_SNAPSHOT = 'MESSAGES_SNAPSHOT',
  CACHE_HIT = 'CACHE_HIT',
  CACHE_MISS = 'CACHE_MISS',
  ERROR = 'ERROR',
  WARNING = 'WARNING',
  RAW = 'RAW',
  CUSTOM = 'CUSTOM',
}

// --- S49 /agui CUSTOM channels (ai.core.agui.translate) ---

export type AimmsCustomChannel =
  | 'aimms.error'
  | 'aimms.toolStatus'
  | 'aimms.question'
  | 'aimms.entities'
  | 'aimms.mediaEvidence'
  | 'aimms.provenance'
  | 'aimms.stateDelta'
  | 'aimms.proposalsRefresh'
  | 'aimms.hitl'
  | 'aimms.custom'
  | 'aimms.evidenceAnalysis'
  | 'aimms.analysisProgress';

// --- Proposal rail (aichat.models) ---

export type ProposalActionType =
  | 'work_order.hold'
  | 'work_order.resume'
  | 'work_order.schedule'
  | 'work_order.resize'
  | 'work_order.update'
  | 'work_order.assign'
  | 'work_order.delete'
  | 'work_order.cancel'
  | 'work_order.transition'
  | 'work_order.create'
  | 'work_order.create_child'
  | 'repair_work_package.create'
  | 'work_order.generate_procurement'
  | 'dependency.create'
  | 'dependency.delete'
  | 'schedule.optimize';

export const PROPOSAL_ACTION_LABELS: Record<ProposalActionType, string> = {
  'work_order.hold': 'Hold work order',
  'work_order.resume': 'Resume work order',
  'work_order.schedule': 'Schedule work order',
  'work_order.resize': 'Resize work order',
  'work_order.update': 'Update work order plan',
  'work_order.assign': 'Assign work order',
  'work_order.delete': 'Delete work order',
  'work_order.cancel': 'Cancel work order',
  'work_order.transition': 'Transition work order lifecycle',
  'work_order.create': 'Create work order',
  'work_order.create_child': 'Create child work order',
  'repair_work_package.create': 'Create repair work package',
  'work_order.generate_procurement': 'Generate procurement child',
  'dependency.create': 'Create dependency',
  'dependency.delete': 'Delete dependency',
  'schedule.optimize': 'Optimize schedule (bulk)',
};

export type ProposalStateType =
  | 'proposed'
  | 'executed'
  | 'rejected'
  | 'expired'
  | 'failed';

// --- Risk radar (repair.risk_models / serializers) ---

export type RiskSeverity =
  | 'critical'
  | 'high'
  | 'medium'
  | 'low';

export type RiskFindingState =
  | 'open'
  | 'acknowledged'
  | 'snoozed'
  | 'resolved'
  | 'dismissed';

export type RiskScanStatus =
  | 'running'
  | 'complete'
  | 'failed'
  | 'aborted';

export const RISK_FINDING_FIELDS = [
  'pk',
  'scope_key',
  'rule_code',
  'rule_version',
  'category',
  'severity',
  'severity_factors',
  'source_model',
  'source_id',
  'title',
  'summary',
  'state',
  'owner',
  'owner_username',
  'first_seen',
  'last_seen',
  'condition_started_at',
  'source_as_of',
  'due_at',
  'due_breached',
  'age_hours',
  'snooze_until',
  'dismiss_recheck_at',
  'reopen_count',
  'version',
] as const;

// --- Voice wire payloads (ai.core.voice.wire) ---

export interface VoiceTransportsAllowed {
  webrtc: boolean;
  relay: boolean;
}

export interface VoiceSessionPayload {
  id: string;
  state: string;
  thread_id: string;
  transport: 'webrtc' | 'relay' | null;
  transports_allowed: VoiceTransportsAllowed;
  webrtc_preview: boolean;
  turn_count: number;
  policy_version: string;
  terminal_reason: string | null;
  analysis_scope_version: number;
}

export interface VoiceSpokenPayload {
  utterance_id: string;
  spoken_summary: string;
  spoken_summary_hash: string;
  playback_state: string;
}

export interface VoicePendingQuestionOption {
  id: string;
  label: string;
  kind: string | null;
  description: string | null;
  recommended: boolean | null;
}

export interface VoicePendingQuestion {
  kind: string;
  interrupt_id: string;
  question_text: string;
  options: VoicePendingQuestionOption[];
  expires_at: string | null;
  source: string | null;
}

export interface VoiceTurnResponse {
  session_id: string;
  thread_id: string;
  turn_id: string;
  message: string;
  workflow_used: string | null;
  response_state: string;
  replayed: boolean;
  spoken: VoiceSpokenPayload | null;
  pending_question: VoicePendingQuestion | null;
}

export type ServerVoiceErrorCode =
  | 'VOICE_SESSION_UNAVAILABLE'
  | 'VOICE_SESSION_FORBIDDEN'
  | 'VOICE_SESSION_LIMIT'
  | 'VOICE_SESSION_EXPIRED'
  | 'IDEMPOTENCY_CONFLICT'
  | 'VOICE_SIGNALING_FAILED'
  | 'VOICE_TRANSPORT_UNAVAILABLE'
  | 'VOICE_TRANSCRIPT_INCOMPLETE'
  | 'VOICE_RESPONSE_INCOMPLETE'
  | 'VOICE_SCOPE_CHANGED';

// --- Analysis scope (ai.core.analysis.scope / .wire) ---

export type AnalysisScopeMode =
  | 'all_authorized_assets'
  | 'explicit_assets'
  | 'legacy_unconfirmed'
  | 'site_group';

export type AnalysisSourceClass =
  | 'controlled_document'
  | 'asset_attachment'
  | 'work_order'
  | 'maintenance_record';

export interface AnalysisScopeDateWindow {
  from: string | null;
  to: string | null;
}

export interface AnalysisScopePayload {
  schema_version: number;
  mode: AnalysisScopeMode;
  machine_ids: number[];
  date_window: AnalysisScopeDateWindow;
  source_classes: AnalysisSourceClass[];
  display_label: string;
}

export interface AnalysisScopeUpdate {
  mode: AnalysisScopeMode;
  machine_ids?: number[] | null;
  date_window?: AnalysisScopeDateWindow | null;
  source_classes?: AnalysisSourceClass[] | null;
  display_label?: string | null;
}

export interface ActiveScopeSummary {
  mode: AnalysisScopeMode;
  version: number;
  display_label: string;
}

export interface ThreadScopePayload {
  thread_id: string;
  scope: AnalysisScopePayload;
  version: number;
  hash: string;
  display_label: string;
  editable: boolean;
}

export interface ThreadScopeUpdateRequest {
  expected_version: number;
  scope: AnalysisScopeUpdate;
}

export type ScopeErrorCode =
  | 'scope_version_conflict'
  | 'scope_update_rejected';

// --- Quota / admission (ai.core.quota.wire) ---

export type QuotaErrorCode =
  | 'token_budget_exhausted'
  | 'rate_limited'
  | 'ai_capacity_busy'
  | 'quota_store_unavailable';

export type PilotErrorCode =
  | 'pilot_stopped'
  | 'pilot_latch_unavailable';

export interface QuotaWindowStatus {
  limit: number;
  used: number;
  remaining: number;
  reset_after_s: number;
}

export interface QuotaTokenLevel {
  used: number;
  reserved: number;
  remaining: number;
  cap: number;
  reset_after_s: number;
}

export interface QuotaStoreStatus {
  healthy: boolean;
  shared: boolean;
}

export interface QuotaPreflightPayload {
  profile: string;
  policy_version: number;
  tokens: Record<string, QuotaTokenLevel>;
  requests: Record<string, QuotaWindowStatus>;
  store: QuotaStoreStatus;
  fits: boolean | null;
  pilot_stopped: boolean | null;
}

// --- Evidence analysis v2 (ai.core.analysis.schemas / .wire) ---

export type EvidenceClassification =
  | 'documented'
  | 'calculated'
  | 'inferred'
  | 'insufficient';

export type AnalysisProgressStage =
  | 'confirming_scope'
  | 'reviewing_records'
  | 'validating_evidence';

export type AnalysisNoDataReason =
  | 'complete_population_no_matches'
  | 'outside_active_selection'
  | 'unauthorized_or_unavailable'
  | 'retrieval_failure'
  | 'unresolved_applicability'
  | 'incomplete_coverage';

export interface RetrievalCoveragePayload {
  population_count: number;
  returned_count: number;
  complete_population: boolean;
  display_truncated: boolean;
  date_field: string | null;
  timezone: string | null;
  filters: string[];
  as_of: string;
  snapshot_label: string | null;
  excluded_null_date_count: number | null;
  incomplete_reason: string | null;
}

export interface CitationLocator {
  page: number | null;
  section: string | null;
  field: string | null;
}

export interface CitationManifestEntry {
  ordinal: number;
  source_type: string;
  source_id: string | null;
  source_title: string | null;
  source_revision: string | null;
  source_class: string | null;
  controlled: boolean;
  as_of: string;
  available: boolean;
  locator: CitationLocator | null;
  applicability: string | null;
  evidence_set_id: string | null;
  calculation: string | null;
}

export interface ClaimPayload {
  claim_id: string;
  claim_role: string;
  claim_type: string;
  evidence_classification: EvidenceClassification;
  citation_ordinals: number[];
  entity_refs: string[];
}

export interface AnalysisScopeStamp {
  display_label: string;
  version: number;
}

export interface AnalysisIncompleteReasonPayload {
  code: string;
  facet: string;
}

export interface EvidenceSetMember {
  member_index: number;
  source_class: string;
  source_object_id: string | null;
  label: string | null;
  available: boolean;
}

export interface EvidenceSetPage {
  members: EvidenceSetMember[];
  population_count: number;
  complete: boolean;
  next_cursor: string | null;
}
