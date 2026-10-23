import type {
  ProcurementEvent,
  ProcurementLineContext,
  ProcurementOrderContext,
  ProcurementSourceSnapshot
} from '../../lib/types/Procurement';
import type { MaterialReadout } from '../../src/pages/tasks/workorders/materials';
export const evidence = {
  attachment_id: 91,
  sha256: 'a'.repeat(64),
  page: 1,
  revision: 'Fixture revision 1'
};
export const source = {
  schema_version: 'r8.1',
  machine_id: 18,
  machine_client_id: 7,
  actor: { pk: 11, label: 'Fixture Buyer', kind: 'internal_person' },
  authority: 'evidence_only',
  approval_policy: 'named_approver_per_po',
  operation_key: 'fixture-source-1',
  expected_version: 3,
  kit_line_id: 71,
  source_at: '2026-10-23T09:00:00+03:00',
  provenance: {
    mode: 'recorded_fixture',
    method: 'recorded_document',
    reference: 'Explicit lane A fixture',
    fixture_namespace: 'r8-fixture:lane-a'
  },
  requirement_version: 2,
  configuration_revision: 'b'.repeat(64),
  requirement_revision: 'c'.repeat(64),
  needed_at: '2026-10-25T12:00:00+03:00',
  deadline_source: evidence,
  dependent_step: 'Seal replacement',
  technical_evidence: [evidence],
  offers: [
    {
      key: 'fixture-quote-a',
      supplier_part_id: 61,
      supplier_id: 31,
      part_id: 41,
      supplier_sku: 'FIXTURE-SEAL-KIT',
      base_uom: 'kit',
      quote_reference: 'Fixture quote A',
      revision: '1',
      order_quantity: '1',
      order_uom: 'kit',
      base_units_per_order_unit: '1',
      unit_price: '620.00',
      discount: '0',
      freight: '45.00',
      tax: '0',
      other_charges: '0',
      delivered_total: '665.00',
      currency: 'USD',
      terms: 'Fixture NET30',
      promised_at: '2026-10-25T09:00:00+03:00',
      promise_basis: 'supplier_quote',
      valid_through: '2099-01-01T00:00:00Z',
      technical_eligibility: 'supported',
      source: evidence
    },
    {
      key: 'fixture-quote-b',
      supplier_part_id: 62,
      supplier_id: 32,
      part_id: 41,
      supplier_sku: 'FIXTURE-LATE-KIT',
      base_uom: 'kit',
      quote_reference: 'Fixture quote B',
      revision: '1',
      order_quantity: '1',
      order_uom: 'kit',
      base_units_per_order_unit: '1',
      unit_price: '540.00',
      discount: '0',
      freight: '40.00',
      tax: '0',
      other_charges: '0',
      delivered_total: '580.00',
      currency: 'USD',
      terms: null,
      promised_at: '2026-10-27T09:00:00+03:00',
      promise_basis: 'supplier_quote',
      valid_through: null,
      technical_eligibility: 'unresolved',
      source: evidence
    }
  ],
  selected_offer_key: 'fixture-quote-a',
  reason:
    'Fixture quote A arrives before seal replacement; quote B arrives later.',
  supersedes_event_id: null
} as ProcurementSourceSnapshot;
const envelope = <T>(
  type: string,
  snapshot: T,
  id = 101
): ProcurementEvent<T> => ({
  event_id: id,
  operation_key: `fixture-${id}`,
  event_type: type,
  source_at: '2026-10-23T09:00:00+03:00',
  recorded_at: '2026-10-23T09:10:00+03:00',
  actor: { pk: 11, label: 'Fixture Buyer', kind: 'internal_person' },
  snapshot
});
export const order: ProcurementOrderContext = {
  purchase_order_id: 51,
  reference: 'PO-FIXTURE-51',
  status: 20,
  status_label: 'Placed',
  supplier_id: 31,
  purchase_order_line_id: 81,
  supplier_part_id: 61,
  order_quantity: '1',
  received_quantity: '0',
  order_uom: 'kit',
  packaging: null,
  base_uom: 'kit',
  base_units_per_order_unit: '1',
  currency: 'USD',
  goods_total: '620.00',
  charges_total: '45.00',
  order_total: '665.00',
  native_recorded_total: '665.00',
  extra_line_ids: [88],
  placed: { date: '2026-10-23', precision: 'date', actor: null },
  target_date: '2026-10-25',
  binding: envelope(
    'PROC_PO_LINKED',
    {
      schema_version: 'r8.1',
      machine_id: 18,
      machine_client_id: 7,
      kit_line_id: 71,
      purchase_order_id: 51,
      purchase_order_line_id: 81,
      source_event_id: 101,
      shortage_id: 99,
      approval_execution: {
        operation_key: 'fixture-approval',
        approval_id: '12345678-1234-1234-1234-123456789abc',
        revision: 1,
        review_hash: 'd'.repeat(64),
        actor_id: 12,
        actor_label: 'Fixture Named Approver',
        result: {
          effect_ref: 'order:51',
          payload: { order_id: 51, line_ids: [81] }
        }
      }
    },
    102
  ),
  recorded_email: null,
  recorded_document: null,
  email_status: 'not_recorded',
  acknowledgment: null,
  acknowledgment_current: null
};
export function materials(state = 'ordered'): MaterialReadout {
  const linked = structuredClone(order);
  if (
    ['recorded', 'acknowledged', 'reserved', 'pending', 'consumed'].includes(
      state
    )
  ) {
    linked.recorded_document = {
      pk: 90,
      order_id: 51,
      operation_key: 'fixture-document',
      state: 'recorded_not_sent',
      supplier_email_sent: false,
      attachment_state: 'verified',
      pdf_sha256: 'e'.repeat(64),
      email_sha256: 'f'.repeat(64),
      context: { provenance_kind: 'fixture' },
      recorded_at: '2026-10-23T09:20:00+03:00'
    };
    linked.email_status = 'recorded_only';
  }
  if (['acknowledged', 'reserved', 'pending', 'consumed'].includes(state)) {
    linked.acknowledgment = envelope(
      'PROC_PO_ACKNOWLEDGED',
      {
        ...source,
        purchase_order_id: 51,
        purchase_order_line_id: 81,
        supplier_part_id: 61,
        supplier_id: 31,
        order_quantity: '1',
        order_uom: 'kit',
        base_uom: 'kit',
        base_units_per_order_unit: '1',
        order_revision: 'g'.repeat(64),
        supplier_reference: 'FIXTURE-ACK-1',
        accepted_quantity: '1',
        terms: 'Fixture NET30',
        promised_at: '2026-10-25T09:00:00+03:00',
        source: evidence,
        deviations: []
      },
      104
    );
    linked.acknowledgment_current = true;
  }
  const hasReceipt = ['reserved', 'pending', 'consumed'].includes(state);
  const line: ProcurementLineContext = {
    kit_line_id: 71,
    key: 'fixture-kit-line-71',
    part_id: 41,
    required_quantity: '1',
    base_uom: 'kit',
    requirement_version: 2,
    requirement_revision: 'c'.repeat(64),
    configuration_revision: 'b'.repeat(64),
    work_due_date: '2026-10-25',
    needed_at: state === 'shortage' ? null : source.needed_at,
    dependent_step: state === 'shortage' ? null : 'Seal replacement',
    source_selection:
      state === 'shortage'
        ? null
        : envelope('PROC_SOURCE_SELECTED', structuredClone(source)),
    selection_current: state === 'shortage' ? null : state !== 'stale',
    reserved_quantity: state === 'reserved' ? '1' : '0',
    issued_quantity: '0',
    used_quantity: state === 'consumed' ? '1' : '0',
    outstanding_quantity: ['reserved', 'consumed'].includes(state) ? '0' : '1',
    orders: ['shortage', 'selected', 'stale'].includes(state) ? [] : [linked],
    receipts: [],
    receipt_allocations: [],
    receipt_outcomes: []
  };
  if (hasReceipt) {
    linked.received_quantity = '1';
    line.receipts = [
      envelope(
        'PROC_RECEIPT_RECORDED',
        {
          schema_version: 'r8.1',
          machine_id: 18,
          machine_client_id: 7,
          kit_line_id: 71,
          purchase_order_id: 51,
          purchase_order_line_id: 81,
          operation_key: 'fixture-receipt',
          po_line: 81,
          kit_line: 71,
          shortage: 99,
          received_before: '0',
          received_after: '1',
          stocks: [
            {
              stock_item: 201,
              quantity: '1',
              batch: 'FIXTURE-BATCH-A',
              location: 33,
              status: 10,
              status_custom_key: null,
              expiry_date: null
            }
          ]
        },
        105
      )
    ];
    line.receipt_outcomes = [
      {
        receipt_id: 1,
        event_id: 105,
        operation_key: 'fixture-receipt',
        reservation_state: state === 'pending' ? 'attention' : 'allocated',
        reservation_code: state === 'pending' ? 'RESERVATION_FAILED' : '',
        allocation_ids: state === 'pending' ? [] : [301]
      }
    ];
  }
  return {
    work_order: 826,
    source: 'job_kit',
    kits_enabled: true,
    stock_visible: true,
    component: null,
    procurement: {
      schema_version: 'r8.1',
      work_order_id: 826,
      work_lifecycle_version: 3,
      kit_version: 4,
      purchasing_visible: true,
      stock_visible: true,
      approval_policy: 'named_approver_per_po',
      email_policy: 'recorded_only',
      lines: [line],
      pending_operations: []
    },
    requirements: [
      {
        key: line.key,
        part: {
          pk: 41,
          name: 'Fixture seal/service kit',
          ipn: 'FIXTURE-SK',
          units: 'kit',
          virtual: false
        },
        requested_part: null,
        quantity: 1,
        required: true,
        fit_basis: {
          source: 'manual',
          substitution_policy: 'exact',
          note: 'Representative technical source'
        },
        reservations: ['reserved', 'consumed'].includes(state)
          ? [
              {
                id: 301,
                quantity: 1,
                status: state === 'consumed' ? 'consumed' : 'reserved',
                stock_item: {
                  pk: 201,
                  hidden: false,
                  batch: 'FIXTURE-BATCH-A',
                  location: 'Fixture stores',
                  location_pk: 33
                },
                reserved: {
                  by: 'Fixture Stores',
                  at: '2026-10-25T09:10:00+03:00'
                },
                staged: null,
                issued: null,
                closed: null,
                usage: []
              }
            ]
          : [],
        shortages: ['reserved', 'consumed'].includes(state)
          ? []
          : [
              {
                id: 99,
                quantity: 1,
                status: line.orders.length ? 'ordered' : 'open',
                purchase_order_line: line.orders.length ? 81 : null,
                reason: ''
              }
            ],
        availability_check: null
      }
    ],
    unmatched_usage: []
  };
}
