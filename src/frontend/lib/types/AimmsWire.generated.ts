// GENERATED FILE — DO NOT EDIT.
//
// Source of truth: backend definitions (see
// aichat/management/commands/generate_wire_contract.py). Regenerate with:
//     python manage.py generate_wire_contract
// CI runs `generate_wire_contract --check` and fails on drift.

// --- Cached AI runtime availability (ai.core.runtime) ---

export interface RuntimeAvailability {
  state: 'starting' | 'ready' | 'transiently_unavailable' | 'permanently_failed' | 'stopping';
  available: boolean;
  reason: 'provider_throttled' | 'provider_unreachable' | 'authentication' | 'configuration' | 'model_pin' | 'initialization' | null;
  retry_after_s: number | null;
}

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
  | 'aimms.custom'
  | 'aimms.evidenceAnalysis'
  | 'aimms.analysisProgress'
  | 'aimms.contextUsed'
  | 'aimms.threadFocus'
  | 'aimms.presentation';

// --- Proposal rail (aichat.models) ---

export type ProposalActionType =
  | 'memory.remember'
  | 'memory.update'
  | 'memory.forget'
  | 'memory.forget_all'
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
  | 'schedule.optimize'
  | 'stock.add'
  | 'stock.remove'
  | 'stock.transfer'
  | 'stock.count'
  | 'investigation.artifact'
  | 'procedure.complete'
  | 'closeout.consent'
  | 'closeout.accept'
  | 'closeout.handoff';

export const PROPOSAL_ACTION_LABELS: Record<ProposalActionType, string> = {
  'memory.remember': 'Remember a memory',
  'memory.update': 'Correct a memory',
  'memory.forget': 'Forget a memory',
  'memory.forget_all': 'Forget all memories',
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
  'stock.add': 'Add stock',
  'stock.remove': 'Remove stock',
  'stock.transfer': 'Transfer stock',
  'stock.count': 'Count stock',
  'investigation.artifact': 'Publish investigation evidence',
  'procedure.complete': 'Complete procedure step',
  'closeout.consent': 'Consent to closeout dictation',
  'closeout.accept': 'Accept closeout note',
  'closeout.handoff': 'Hand off accepted closeout note',
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

export interface VoiceClientTiming {
  speech_to_final_ms?: number | null;
  final_to_submit_ms?: number | null;
  ms_queue?: number | null;
  ack_schedule_ms?: number | null;
  submit_to_observed_playback_ms?: number | null;
  feedback_onset_ms?: number | null;
  answer_onset_ms?: number | null;
  local_stop_ms?: number | null;
}

export interface VoiceTimingReport {
  epoch: string;
  utterance_id: string;
  spoken_hash: string;
  provenance: 'rtp_energy_proxy' | 'local_pause_proxy';
  first_playback_epoch_ms?: number | null;
  onset_kind?: 'filler' | 'answer' | null;
  coverage_reason?: 'not_observed' | 'hidden_page' | 'canceled' | 'no_audio_proxy' | 'late_or_out_of_order' | null;
  generation?: number | null;
  timing: VoiceClientTiming;
}

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
  locale: string;
  voice: string;
  consent_version: string;
  focus: unknown | null;
  focus_version: number;
  speech_utterance_id?: string | null;
  speech_playback_state?: string | null;
  speech_audit_state?: 'unavailable' | 'confirmed' | 'uncertain' | null;
}

export interface VoiceSpokenPayload {
  utterance_id: string;
  spoken_summary: string;
  spoken_summary_hash: string;
  playback_state: string;
  playback_audit_state?: 'unavailable' | 'confirmed' | 'uncertain';
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

export interface VoicePresentationPayload {
  id: string;
  source_hash: string;
  index: number;
  total: number;
}

export interface VoiceDecisionContext {
  decision_id: string;
  sequence: number;
  revision: number | string;
  preview_hash: string | null;
}

export interface VoiceDecisionSection {
  id: string;
  label: string;
  text: string;
}

export interface VoicePendingDecision {
  decision_id: string;
  sequence: number;
  revision: number | string;
  preview_hash: string | null;
  kind: string;
  source_id: string;
  state: string;
  target_label: string;
  sections: VoiceDecisionSection[];
  required_review_sections: string[];
  allowed_responses: string[];
  required_phrase: string | null;
  locale: string;
  voice_eligible: boolean;
  voice_ineligible_reason: string | null;
  expires_at: string;
  utterance_id: string | null;
  delivery_state: string;
  review_acknowledged: boolean;
  operation_id: string | null;
  execution_state: string | null;
  receipt_ref: string | null;
  spoken_summary: string;
  spoken_summary_hash: string;
}

export interface VoiceDecisionEvent {
  kind: string;
  decision_id: string | null;
  sequence: number | null;
  message: string;
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
  pending_decision: VoicePendingDecision | null;
  decision_event: VoiceDecisionEvent | null;
  presentation: VoicePresentationPayload | null;
  focus: unknown | null;
  focus_version: number;
  focus_refresh_required: boolean;
}

export type ServerVoiceErrorCode =
  | 'VOICE_TIMING_UNAVAILABLE'
  | 'VOICE_SESSION_UNAVAILABLE'
  | 'VOICE_SESSION_FORBIDDEN'
  | 'VOICE_SESSION_LIMIT'
  | 'VOICE_SESSION_EXPIRED'
  | 'IDEMPOTENCY_CONFLICT'
  | 'VOICE_SIGNALING_FAILED'
  | 'VOICE_TRANSPORT_UNAVAILABLE'
  | 'VOICE_TRANSCRIPT_INCOMPLETE'
  | 'VOICE_RESPONSE_INCOMPLETE'
  | 'VOICE_SCOPE_CHANGED'
  | 'VOICE_FOCUS_CHANGED'
  | 'VOICE_PROMPT_UNKNOWN'
  | 'VOICE_DECISION_UNAVAILABLE'
  | 'VOICE_DECISION_CONFLICT'
  | 'VOICE_OPERATION_NOT_FOUND'
  | 'VOICE_CONSENT_REQUIRED'
  | 'VOICE_LOCALE_UNSUPPORTED'
  | 'VOICE_PRESENTATION_UNAVAILABLE'
  | 'VOICE_SAMPLE_UNAVAILABLE'
  | 'VOICE_SAMPLE_LIMIT';

// --- Voice request models (ai.core.voice.routes) ---

export interface VoiceSessionCreateRequest {
  thread_id?: string | null;
  locale?: string;
  voice?: string;
  consent_version?: string;
  expected_focus_version?: number | null;
}

export interface VoiceTurnRequest {
  transcript: string;
  item_id: string;
  confidence?: number | null;
  language?: string;
  decision_context?: Record<string, unknown> | null;
  timing?: VoiceClientTiming | null;
  expected_focus_version?: number | null;
  progress_mode?: 'off' | 'brief' | 'guided';
}

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

// --- Conversation focus (ai.core.focus.contract / .wire) ---

export type ConversationFocusKind =
  | 'machine'
  | 'kanban_card'
  | 'work_order';

export interface ConversationFocusRef {
  kind: ConversationFocusKind;
  id: number;
}

export interface ConversationFocusPayload {
  kind: ConversationFocusKind;
  id: number;
  label: string;
  machine_id: number | null;
  work_order_id: number | null;
}

export interface ThreadFocusPayload {
  focus: ConversationFocusPayload | null;
  focus_version: number;
  focus_unavailable: boolean;
}

export interface ThreadFocusUpdateRequest {
  focus: ConversationFocusRef | null;
  expected_focus_version: number;
}

export interface FocusCandidateSearchResult {
  candidates: ConversationFocusPayload[];
  returned_count: number;
  has_more: boolean;
}

export type FocusErrorCode =
  | 'focus_version_conflict'
  | 'focus_update_rejected'
  | 'focus_out_of_analysis_scope'
  | 'focus_decision_not_set_aside';

// --- Work focus (aichat.services.work_focus / ai.core.analysis.wire) ---

export interface ComponentFocusReference {
  machine_id: number;
  rule: 'reference_positions.v1';
  position_id: string;
  slot_code: string | null;
  revision: string;
}

export interface EvidenceFocusReference {
  kind: 'attachment' | 'closeout_reading' | 'media';
  id: number;
  revision: string;
}

export interface WorkFocusIdentity {
  schema_version: number;
  kind: 'work_order' | 'reference_position';
  work_order_id: number | null;
  lifecycle_version: number | null;
  machine_id: number | null;
  request_id?: number | null;
  request_version?: number | null;
  component?: ComponentFocusReference | null;
  evidence?: EvidenceFocusReference | null;
}

export interface WorkFocusAcknowledgeRequest {
  work_order_id: number | null;
  expected_version: number | null;
  request_id?: number | null;
  request_version?: number | null;
  component?: ComponentFocusReference | null;
  evidence?: EvidenceFocusReference | null;
}

export interface WorkFocusAcknowledgement {
  schema_version: number;
  thread_id: string;
  focus: WorkFocusIdentity;
  receipt: string;
  expires_in: number;
}

export interface SavedTurnWorkFocus {
  thread_id: string;
  turn_id: string;
  focus: WorkFocusIdentity;
  stale: boolean;
}

export type WorkFocusErrorCode =
  | 'work_focus_rejected';

export type WorkFocusState =
  | 'current'
  | 'changed'
  | 'unavailable';

// --- Quota / admission (ai.core.quota.wire) ---

// --- Optional read-only presentation (ai.core.presentation.wire) ---

export interface PresentationReferenceV1 {
  ref_id: string;
  kind: 'entity' | 'evidence' | 'metric_dataset' | 'trend_dataset';
  source_key: string;
}

export interface AssistantPresentationV1 {
  schema_version: 1;
  format: 'openui-lang';
  library: 'aimms-chat@1';
  program: string;
  references: PresentationReferenceV1[];
}

export interface PresentationSampleV1 {
  observed_at: string;
  value: number | null;
  quality: 'good' | 'uncertain' | 'bad' | 'missing';
}

export interface PresentationTrendV1 {
  source_key: string;
  kind: 'trend_dataset';
  unit: string;
  source: string;
  source_revision: string;
  as_of: string;
  timezone: string;
  samples: PresentationSampleV1[];
}

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

// --- Thread listing and memory inspection (ai.core.app) ---

export interface ThreadInfo {
  thread_id: string;
  title: string;
  message_count: number;
  turn_count: number;
  summary: string;
  created_at: string | null;
  last_activity: string | null;
  is_persisted: boolean;
  shared: boolean;
  active_scope: ActiveScopeSummary | null;
}

export interface ThreadMemoryItem {
  id: string;
  text: string;
  lifecycle: string;
  memory_type: string;
  created_seq: number;
  superseded_by: string | null;
  directive_flags: string[];
}

export interface ThreadMemoryResponse {
  thread_id: string;
  label: string;
  through_sequence: number;
  latest_sequence: number;
  body_version: number;
  open_questions: ThreadMemoryItem[];
  pending_proposals: ThreadMemoryItem[];
  machine_facts: ThreadMemoryItem[];
  corrections: ThreadMemoryItem[];
  citation_keys: string[];
  narrative: string;
  exclusions_count: number;
}

export interface ThreadMemoryCorrectionRequest {
  item_id: string;
  action: 'wrong' | 'forget';
}

export interface ThreadMemoryCorrectionResponse {
  thread_id: string;
  item_id: string;
  result: 'applied' | 'unknown_item';
  through_sequence: number;
}

// --- Typed chat request/response (ai.core.app) ---

export interface ChatRequest {
  message: string;
  thread_id?: string | null;
  user_id?: string;
  context?: Record<string, unknown> | null;
  file_ids?: string[] | null;
  modality?: string;
  modality_metadata?: Record<string, unknown> | null;
  idempotency_key?: string | null;
  correlation_id?: string | null;
  expected_scope_version?: number | null;
  expected_focus_version?: number | null;
  work_focus_receipt?: string | null;
}

export interface ChatResponse {
  thread_id: string;
  message: string;
  agent: string;
  workflow_used: string | null;
  focus: ConversationFocusPayload | null;
  focus_version: number;
  focus_refresh_required: boolean;
}
