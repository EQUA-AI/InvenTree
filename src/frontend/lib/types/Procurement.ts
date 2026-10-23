/** R8.1 additive Materials procurement contract. No purchase or stock FSM.
 * GET tasks/work-orders/:id/materials/ -> procurement.
 * All quantities/costs are Decimal strings; null means unknown.
 * Instants are ISO 8601 with timezone. Native date-only facts remain dates.
 */
export type ProcurementDecimal = string;
export type ProcurementInstant = string;

export interface ProcurementEvidence {
  attachment_id: number;
  sha256: string;
  page: number;
  revision: string;
}

export interface ProcurementProvenance {
  mode: 'live_test' | 'recorded_fixture';
  method: 'human_review' | 'recorded_document' | 'model_execution';
  reference: string;
  fixture_namespace: string | null;
}

export interface ProcurementActor {
  pk: number | null;
  label: string | null;
  kind: 'internal_person' | null;
}

export interface ProcurementEvent<T> {
  event_id: number;
  operation_key: string;
  event_type: string;
  source_at: ProcurementInstant | null;
  recorded_at: ProcurementInstant;
  actor: ProcurementActor;
  snapshot: T;
}

export interface ProcurementOffer {
  key: string;
  supplier_part_id: number;
  quote_reference: string;
  revision: string;
  order_quantity: ProcurementDecimal;
  order_uom: string;
  base_units_per_order_unit: ProcurementDecimal;
  unit_price: ProcurementDecimal | null;
  /** Absolute goods discount, in the offer currency. */
  discount: ProcurementDecimal | null;
  freight: ProcurementDecimal | null;
  tax: ProcurementDecimal | null;
  other_charges: ProcurementDecimal | null;
  delivered_total: ProcurementDecimal | null;
  currency: string;
  terms: string | null;
  promised_at: ProcurementInstant | null;
  promise_basis: 'supplier_quote' | 'estimate' | 'unknown';
  valid_through: ProcurementInstant | null;
  technical_eligibility: 'supported' | 'unresolved' | 'not_eligible';
  source: ProcurementEvidence;
}

export interface ProcurementEvidenceCommand {
  operation_key: string;
  expected_version: number;
  kit_line_id: number;
  source_at: ProcurementInstant;
  provenance: ProcurementProvenance;
}

/** POST procurement/source-selection/. Reuse the exact body/key on replay. */
export interface ProcurementSourceCommand extends ProcurementEvidenceCommand {
  requirement_version: number;
  configuration_revision: string;
  needed_at: ProcurementInstant | null;
  deadline_source: ProcurementEvidence | null;
  dependent_step: string | null;
  technical_evidence: ProcurementEvidence[];
  offers: ProcurementOffer[];
  selected_offer_key: string;
  reason: string;
  supersedes_event_id: number | null;
}

export interface ProcurementSnapshot {
  schema_version: 'r8.1';
  machine_id: number;
  machine_client_id: number;
  actor: ProcurementActor;
  authority: 'evidence_only';
  approval_policy: 'named_approver_per_po';
}

export interface ProcurementSourceSnapshot
  extends Omit<ProcurementSourceCommand, 'offers'>,
    ProcurementSnapshot {
  requirement_revision: string;
  offers: (ProcurementOffer & {
    supplier_id: number;
    part_id: number;
    supplier_sku: string;
    base_uom: string;
  })[];
}

/** POST procurement/acknowledgment/. Acceptance is in native order units. */
export interface ProcurementAckCommand extends ProcurementEvidenceCommand {
  purchase_order_line_id: number;
  supplier_reference: string;
  accepted_quantity: ProcurementDecimal;
  terms: string;
  promised_at: ProcurementInstant;
  source: ProcurementEvidence;
  deviations: string[];
}

export interface ProcurementAckSnapshot
  extends ProcurementAckCommand,
    ProcurementSnapshot {
  purchase_order_id: number;
  supplier_part_id: number;
  supplier_id: number;
  order_quantity: ProcurementDecimal;
  order_uom: string | null;
  base_uom: string;
  base_units_per_order_unit: ProcurementDecimal;
  order_revision: string;
}

/** Producer contract for B05. This records files, never transport acceptance. */
export interface ProcurementEmailSnapshot extends ProcurementSnapshot {
  kit_line_id: number;
  purchase_order_id: number;
  purchase_order_line_id: number;
  source_event_id: number;
  source_at: ProcurementInstant;
  provenance: ProcurementProvenance;
  pdf: ProcurementEvidence;
  email_attachment_id: number;
  email_sha256: string;
}

/** B04 appends binding after validated native linking/approval, never edits selection. */
export interface ProcurementBindingSnapshot extends ProcurementSnapshot {
  kit_line_id: number;
  purchase_order_id: number;
  purchase_order_line_id: number;
  source_event_id: number;
  source_at: ProcurementInstant;
  provenance: ProcurementProvenance;
  approval: {
    approval_id: number;
    approver: ProcurementActor;
    approved_at: ProcurementInstant;
  } | null;
  placed: { at: ProcurementInstant; actor: ProcurementActor } | null;
}

/** Producer contract for B07: exact returned stock, never a Part-based join. */
export interface ProcurementReceiptSnapshot extends ProcurementSnapshot {
  kit_line_id: number;
  purchase_order_id: number;
  purchase_order_line_id: number;
  source_at: ProcurementInstant;
  provenance: ProcurementProvenance;
  received_quantity_before: ProcurementDecimal;
  received_quantity_after: ProcurementDecimal;
  stocks: {
    stock_item_id: number;
    quantity_at_receipt: ProcurementDecimal;
    base_uom: string;
    batch: string;
    location_id: number;
    status_code: number;
  }[];
}

export interface ProcurementOrderContext {
  purchase_order_id: number;
  reference: string;
  status: number;
  status_label: string;
  supplier_id: number;
  purchase_order_line_id: number;
  supplier_part_id: number | null;
  order_quantity: ProcurementDecimal;
  received_quantity: ProcurementDecimal;
  order_uom: string | null;
  packaging: string | null;
  base_uom: string | null;
  base_units_per_order_unit: ProcurementDecimal | null;
  currency: string;
  goods_total: ProcurementDecimal | null;
  charges_total: ProcurementDecimal | null;
  order_total: ProcurementDecimal | null;
  native_recorded_total: ProcurementDecimal | null;
  extra_line_ids: number[];
  placed: { date: string; precision: 'date'; actor: null } | null;
  target_date: string | null;
  binding: ProcurementEvent<ProcurementBindingSnapshot> | null;
  recorded_email: ProcurementEvent<ProcurementEmailSnapshot> | null;
  email_status: 'recorded_only' | 'not_recorded';
  acknowledgment: ProcurementEvent<ProcurementAckSnapshot> | null;
  acknowledgment_current: boolean | null;
}

export interface ProcurementLineContext {
  kit_line_id: number;
  key: string;
  part_id: number;
  required_quantity: ProcurementDecimal;
  base_uom: string;
  requirement_version: number;
  requirement_revision: string;
  configuration_revision: string;
  work_due_date: string | null;
  needed_at: ProcurementInstant | null;
  dependent_step: string | null;
  source_selection: ProcurementEvent<ProcurementSourceSnapshot> | null;
  selection_current: boolean | null;
  reserved_quantity: ProcurementDecimal;
  issued_quantity: ProcurementDecimal;
  used_quantity: ProcurementDecimal;
  outstanding_quantity: ProcurementDecimal;
  orders: ProcurementOrderContext[];
  receipts: ProcurementEvent<ProcurementReceiptSnapshot>[];
}

export interface MaintenanceProcurementContext {
  schema_version: 'r8.1';
  work_order_id: number;
  work_lifecycle_version: number;
  kit_version: number | null;
  purchasing_visible: boolean;
  stock_visible: boolean;
  approval_policy: 'named_approver_per_po';
  email_policy: 'recorded_only';
  lines: ProcurementLineContext[];
  pending_operations: {
    command: string;
    idempotency_key: string;
    status: 'pending' | 'uncertain';
    result_ref: string;
  }[];
}
