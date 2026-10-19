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
  narrative: string;
}

export interface CloseoutProposalField {
  value: string | number | null;
  spans: [number, number][];
  confidence: number;
  warnings: string[];
}

export interface CloseoutProposal {
  id: number;
  extractor: string;
  fields: Record<string, CloseoutProposalField>;
  part_candidates: { text: string }[];
  warnings: string[];
  decisions: {
    field_path: string;
    decision: string;
    final_value: unknown;
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
  version: number;
}

export interface CloseoutReadingRow {
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
