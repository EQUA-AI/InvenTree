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

/** Generic Work audit redaction; applies to every procurement event. */
export interface HiddenProcurementMetadata {
  hidden: true;
}

/** Native retained bytes are retrieved through recorded-evidence/, never attachments. */
export interface ProcurementEmailSnapshot {
  schema_version: 'r8.1';
  machine_id: number;
  machine_client_id: number;
  kit_line_id: number;
  purchase_order_id: number;
  purchase_order_line_id: number;
  binding_event_id: number;
  source_event_id: number | null;
  document_evidence_id: number;
  document_operation_key: string;
  pdf_sha256: string;
  email_sha256: string;
  state: 'recorded_not_sent';
  supplier_email_sent: false;
}

export interface ProcurementBindingSnapshot {
  schema_version: 'r8.1';
  machine_id: number;
  machine_client_id: number;
  kit_line_id: number;
  purchase_order_id: number;
  purchase_order_line_id: number;
  source_event_id: number | null;
  shortage_id: number;
  approval_execution: {
    operation_key: string;
    approval_id: string;
    revision: number;
    review_hash: string;
    actor_id: number;
    actor_label: string;
    result: { effect_ref: string | null; payload: Record<string, unknown> };
  } | null;
}

/** Exact immutable P3 stock facts, in native base units. */
export interface ProcurementReceiptStock {
  stock_item: number;
  quantity: ProcurementDecimal;
  batch: string;
  location: number;
  status: number;
  status_custom_key: number | null;
  expiry_date: string | null;
}

export interface ProcurementReceiptSnapshot {
  schema_version: 'r8.1';
  machine_id: number;
  machine_client_id: number;
  kit_line_id: number;
  purchase_order_id: number;
  purchase_order_line_id: number;
  operation_key: string;
  po_line: number;
  kit_line: number;
  shortage: number;
  received_before: ProcurementDecimal;
  received_after: ProcurementDecimal;
  stocks: ProcurementReceiptStock[];
}

export interface ProcurementAllocationSnapshot {
  receipt_id: number;
  operation_key: string;
  purchase_order_id: number;
  purchase_order_line_id: number;
  kit_line_id: number;
  allocation_ids: number[];
  stock_ids: number[];
}

export interface ProcurementRecordedDocument {
  pk: number;
  order_id: number;
  operation_key: string;
  state: 'recorded_not_sent';
  supplier_email_sent: false;
  attachment_state: 'verified' | 'corrupt';
  pdf_sha256: string;
  email_sha256: string;
  context: Record<string, unknown>;
  recorded_at: ProcurementInstant;
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
  recorded_document: ProcurementRecordedDocument | null;
  email_status: 'recorded_only' | 'not_recorded';
  acknowledgment: ProcurementEvent<ProcurementAckSnapshot> | null;
  acknowledgment_current: boolean | null;
}

export interface ProcurementLineContext {
  kit_line_id: number;
  key: string;
  part_id: number | null;
  required_quantity: ProcurementDecimal;
  base_uom: string | null;
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
  receipt_allocations: ProcurementEvent<ProcurementAllocationSnapshot>[];
  receipt_outcomes: {
    receipt_id: number;
    event_id: number;
    operation_key: string;
    reservation_state: 'pending' | 'attention' | 'allocated';
    reservation_code: string;
    allocation_ids: number[];
  }[];
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
