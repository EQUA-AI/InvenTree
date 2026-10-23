import type { ProcurementLineContext } from '@lib/types/Procurement';

export const selectedOffer = (line: ProcurementLineContext) =>
  line.source_selection?.snapshot.offers.find(
    (offer) => offer.key === line.source_selection?.snapshot.selected_offer_key
  );

/** Quotes and current fingerprints alone do not grant authority to purchase. */
export function placementPayload(
  line: ProcurementLineContext,
  now = Date.now()
) {
  const offer = selectedOffer(line);
  if (
    !line.selection_current ||
    !offer ||
    offer.technical_eligibility !== 'supported' ||
    !offer.valid_through ||
    !Number.isFinite(Date.parse(offer.valid_through)) ||
    Date.parse(offer.valid_through) <= now ||
    !offer.promised_at ||
    offer.promise_basis !== 'supplier_quote' ||
    !line.needed_at ||
    !Number.isFinite(Date.parse(line.needed_at)) ||
    !Number.isFinite(Date.parse(offer.promised_at)) ||
    Date.parse(offer.promised_at) > Date.parse(line.needed_at) ||
    [
      offer.unit_price,
      offer.discount,
      offer.freight,
      offer.tax,
      offer.other_charges,
      offer.delivered_total
    ].some((value) => value === null) ||
    Number(offer.discount) !== 0
  )
    return null;
  // Offer discount is an absolute amount. Native PO discount is a percentage:
  // nonzero discounts must be prepared in native purchasing, never copied across.
  return {
    operation: 'place_purchase_order',
    supplier_id: offer.supplier_id,
    currency: offer.currency,
    description:
      line.source_selection!.snapshot.provenance.mode === 'recorded_fixture'
        ? `[Fixture ${line.source_selection!.snapshot.provenance.fixture_namespace}] ${line.source_selection!.snapshot.reason}`
        : line.source_selection!.snapshot.reason,
    line_items: [
      {
        supplier_part_id: offer.supplier_part_id,
        quantity: offer.order_quantity,
        unit_price: offer.unit_price
      }
    ],
    extra_lines: [
      { description: 'Freight', quantity: '1', unit_price: offer.freight },
      ...(Number(offer.tax) > 0
        ? [{ description: 'Quoted tax', quantity: '1', unit_price: offer.tax }]
        : []),
      ...(Number(offer.other_charges) > 0
        ? [
            {
              description: 'Other quoted charges',
              quantity: '1',
              unit_price: offer.other_charges
            }
          ]
        : [])
    ],
    total: offer.delivered_total
  };
}

/** Only an exact native line from the successful approval can be linked. */
export function approvedLine(result: unknown): number | null {
  if (!result || typeof result !== 'object') return null;
  const payload = 'payload' in result ? result.payload : result;
  if (
    !payload ||
    typeof payload !== 'object' ||
    !('line_ids' in payload) ||
    !Array.isArray(payload.line_ids) ||
    payload.line_ids.length !== 1
  )
    return null;
  const id = payload.line_ids[0];
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0
    ? id
    : null;
}

export function commandResult(
  kind: string,
  body: Record<string, unknown>,
  result: unknown
): boolean {
  if (!result || typeof result !== 'object') return false;
  const data = result as Record<string, unknown>;
  if (kind === 'approval')
    return typeof data.id === 'string' && data.action_type === 'purchase_order';
  if (kind === 'link')
    return (
      typeof data.id === 'number' &&
      data.purchase_order_line === body.purchase_order_line_id
    );
  if (kind === 'document')
    return (
      data.operation_key === body.operation_key &&
      data.state === 'recorded_not_sent' &&
      data.supplier_email_sent === false &&
      typeof data.pk === 'number'
    );
  return (
    data.operation_key === body.operation_key &&
    typeof data.event_id === 'number' &&
    data.event_type ===
      (kind === 'source' ? 'PROC_SOURCE_SELECTED' : 'PROC_PO_ACKNOWLEDGED')
  );
}
