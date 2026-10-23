import type { ProcurementActor, ProcurementReceiptStock } from './Procurement';
import type { FittedRecord } from './RecordedFacts';

export interface CaptureOriginal {
  id: number;
  filename: string;
  content_type: string;
  content_hash: string;
  byte_size: number;
  started_at: string | null;
  duration_ms: number | null;
  url: string;
}
export interface AcceptedReportSummary {
  id: number;
  revision: number;
  url: string;
  content_hash: string;
  completed_by: number;
  completed_by_name: string;
  completed_at: string;
  frozen_by: number;
  frozen_by_name: string;
  frozen_at: string;
  verified_by: number | null;
  verified_by_name: string | null;
  verified_at: string | null;
  amendment_id: number | null;
}
export interface CloseoutWorkOrder {
  id: number;
  reference: string;
  title: string;
  lifecycle_status: string;
  lifecycle_version: number;
}

export interface CloseoutCapture {
  originals?: CaptureOriginal[];
  source_spans?: {
    original_id: number;
    transcript_revision: string;
    narrative_code_points: [number, number];
    segments: unknown[];
  }[];
  id: number;
  status: string;
  source_type: string;
  revision: number | null;
  decision_version?: string;
  narrative: string;
  transcript_reference?: string | null;
  voice_unavailable_reason?: string;
  created_by?: number;
  created_by_name?: string | null;
  created_at?: string;
  completed_closeout?: number | null;
  revisions?: {
    id: number;
    revision: number;
    narrative: string;
    source_content_hash: string;
    work_order_version: number;
    supersedes: number | null;
    created_by: number;
    created_by_name?: string | null;
    created_at: string;
  }[];
}

export interface CloseoutProposalField {
  value: string | number | null;
  spans: [number, number][];
  confidence: number;
  warnings: string[];
}

export interface CloseoutProposal {
  id: number;
  decision_version?: string;
  extractor: string;
  fields: Record<string, CloseoutProposalField>;
  part_candidates: { text: string }[];
  warnings: string[];
  decisions: {
    field_path: string;
    decision: string;
    final_value: unknown;
    decided_by?: number;
    decided_by_name?: string | null;
    decided_at?: string;
  }[];
}

export interface CloseoutPartUsageRow {
  id: number;
  source: string;
  state: string;
  candidate_text: string;
  work_order?: number;
  allocation?: number | null;
  part?: number | null;
  stock_item?: number | null;
  stock_tracking_id?: number | null;
  resolved_by?: number | null;
  material?: CloseoutMaterial | null;
  planned_quantity: string | null;
  issued_quantity: string | null;
  used_quantity: string | null;
  disposition: string;
  variance_reason?: string;
  version: number;
}

export interface CloseoutReadingRow {
  focus_revision?: string;
  phase?: string;
  recorded_by?: number;
  recorded_by_name?: string | null;
  recorded_at?: string;
  id: number;
  label: string;
  raw_text: string;
  value: string | null;
  unit: string;
  required: boolean;
  verification_state: string;
  warnings: string[];
}

export interface CloseoutReadinessBlocker {
  code: string;
  message: string;
  blocking: boolean;
}

export interface CloseoutReadiness {
  ready: boolean;
  blockers: CloseoutReadinessBlocker[];
  warnings: CloseoutReadinessBlocker[];
}

export interface CloseoutEffectRow {
  id: number;
  effect_type: string;
  status: string;
  attempts: number;
  result_reference: string;
}

export interface CloseoutAmendment {
  id: number;
  closeout: number;
  status: string;
  reason: string;
  changes: Record<string, { from: unknown; to: unknown }>;
  requested_by: number;
  requested_by_name?: string | null;
  decided_by: number | null;
  decided_by_name?: string | null;
  created_at: string;
  applied_at: string | null;
  base_content_hash: string;
  effective_snapshot: { closeout?: Record<string, unknown> };
}

/** Live supporting records; frozen completed-account snapshots stay separate. */
export interface CloseoutMaterial {
  part: { pk: number; name: string; ipn: string; units: string } | null;
  stock_item: {
    pk: number | null;
    hidden: boolean;
    batch?: string;
    serial?: string;
    location?: string;
  } | null;
  stock_tracking_id: number | null;
  fitted_record: FittedRecord | null;
  issued?: { by: string | null; at: string | null } | null;
  receipts: {
    event_id: number;
    recorded_at: string;
    actor: ProcurementActor;
    stocks: ProcurementReceiptStock[];
    purchase_order_id: number;
    purchase_order_line_id: number;
    reference: string;
    goods_total: string | null;
    charges_total: string | null;
    order_total: string | null;
    currency: string;
  }[];
}
