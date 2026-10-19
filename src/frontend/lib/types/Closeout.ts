export interface CloseoutWorkOrder {
  id: number;
  reference: string;
  title: string;
  lifecycle_status: string;
  lifecycle_version: number;
}

export interface CloseoutCapture {
  id: number;
  status: string;
  source_type: string;
  revision: number | null;
  decision_version?: string;
  narrative: string;
  transcript_reference?: string | null;
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
  planned_quantity: string | null;
  issued_quantity: string | null;
  used_quantity: string | null;
  disposition: string;
  variance_reason?: string;
  version: number;
}

export interface CloseoutReadingRow {
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
