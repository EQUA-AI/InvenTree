/** Transport contracts for retained R8 commands. No inferred commercial outcomes. */
export interface MaintenanceReceiptRequest {
  maintenance_operation_key: string;
  maintenance_requirement_version: number;
  location: number;
  items: [
    {
      line_item: number;
      quantity: string;
      status: number;
      batch_code: string;
      [key: string]: unknown;
    }
  ];
  [key: string]: unknown;
}

export interface ReceiptStockSnapshot {
  pk: number;
  [key: string]: unknown;
}

export interface ProcurementIntent<T = unknown> {
  schema: 1;
  kind: string;
  scope: string;
  request: T;
}

function orderedJSON(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(orderedJSON).join(',')}]`;
  if (value !== null && typeof value === 'object')
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${orderedJSON(item)}`)
      .join(',')}}`;
  const result = JSON.stringify(value);
  if (result === undefined) throw new Error('Request is not JSON');
  return result;
}

/** Normalize exactly like JSON transport before comparing or persisting a body. */
export function canonicalIntent(value: unknown): string {
  const json = JSON.stringify(value);
  if (json === undefined) throw new Error('Request is not JSON');
  return orderedJSON(JSON.parse(json));
}

/** Malformed or cross-family storage must block writes, never be silently replaced. */
export function readProcurementIntent<T>(
  raw: string | null,
  kind: string,
  scope: string
): ProcurementIntent<T> | null {
  if (raw === null) return null;
  const value = JSON.parse(raw);
  if (
    !value ||
    value.schema !== 1 ||
    value.kind !== kind ||
    value.scope !== scope ||
    !value.request ||
    typeof value.request !== 'object' ||
    Array.isArray(value.request)
  )
    throw new Error('Invalid retained operation');
  return value;
}

/** Native receive returns StockItemSerializer[], without a Work lifecycle version. */
export function isReceiptResponse(
  value: unknown
): value is ReceiptStockSnapshot[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every(
      (row) =>
        row !== null &&
        typeof row === 'object' &&
        Number.isSafeInteger(row.pk) &&
        row.pk > 0
    ) &&
    new Set(value.map((row) => row.pk)).size === value.length
  );
}

/** Resolve use only by the exact allocation's native consume tracking, never Part matching. */
export function isConsumedAllocation(
  value: unknown,
  allocationId: number
): boolean {
  if (!value || typeof value !== 'object') return false;
  const row = value as Record<string, unknown>;
  return (
    row.id === allocationId &&
    row.status === 'consumed' &&
    Number.isSafeInteger(row.stock_tracking_id) &&
    Number(row.stock_tracking_id) > 0
  );
}

/** Refresh independent native facts after purchase/document/receipt/custody writes. */
export function procurementRefreshKeys(
  workOrderId: number,
  purchaseOrderId?: number
) {
  return [
    ['work-order-materials', workOrderId],
    ['work-order', workOrderId],
    ['work-order-job-kit', workOrderId],
    ['closeout-part-usage', workOrderId],
    ['closeout-readings', workOrderId],
    ['work-order-readiness', workOrderId],
    ['instance'],
    // Native tables use endpoint-derived keys; invalidate that family as well.
    ['tabledata'],
    ...(purchaseOrderId ? [['purchase-order', purchaseOrderId]] : [])
  ];
}
