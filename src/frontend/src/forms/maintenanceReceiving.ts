import type { ProcurementReceiptStock } from '@lib/types/Procurement';

export interface MaintenanceReceiveRequest {
  maintenance_operation_key: string;
  maintenance_requirement_version: number;
  location: number | null;
  items: Record<string, unknown>[];
}
export interface MaintenanceReceiptReadback {
  id: number;
  operation_key: string;
  actor: number;
  recorded_at: string;
  request: MaintenanceReceiveRequest;
  reservation_state: 'pending' | 'attention' | 'allocated';
  reservation_code: string;
  stocks: ProcurementReceiptStock[];
  allocation_ids: number[];
}
export interface MaintenanceReceivingContext {
  maintenance: boolean;
  binding: {
    work_order: number;
    reference: string;
    kit_line: number;
    purchase_order_line: number;
    requirement_version: number;
  } | null;
  receipts: MaintenanceReceiptReadback[];
}

/** Verify storage before the first POST. Never replace an uncertain intent. */
export function retainReceipt(
  storage: Storage,
  key: string,
  request: MaintenanceReceiveRequest
) {
  const raw = JSON.stringify(request);
  const previous = storage.getItem(key);
  if (previous && previous !== raw) throw new Error('Receipt intent changed');
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw)
    throw new Error('Receipt storage unavailable');
  return JSON.parse(raw) as MaintenanceReceiveRequest;
}

export function validateMaintenanceReceipt(
  request: MaintenanceReceiveRequest,
  line: number
) {
  if (request.items.length !== 1 || request.items[0].line_item !== line)
    return 'line';
  if (!String(request.items[0].batch_code ?? '').trim()) return 'batch';
  if (!(request.items[0].location || request.location)) return 'location';
  return null;
}

/** Existing native receipt storage is untrusted and must never replace a body. */
export function readRetainedReceipt(
  raw: string | null
): MaintenanceReceiveRequest | null {
  if (raw === null) return null;
  const request = JSON.parse(raw);
  if (
    !request ||
    typeof request.maintenance_operation_key !== 'string' ||
    !request.maintenance_operation_key ||
    !Number.isSafeInteger(request.maintenance_requirement_version) ||
    request.maintenance_requirement_version < 1 ||
    !Array.isArray(request.items) ||
    request.items.length !== 1 ||
    !request.items[0] ||
    !Number.isSafeInteger(request.items[0].line_item) ||
    request.items[0].line_item < 1 ||
    validateMaintenanceReceipt(request, request.items[0].line_item)
  )
    throw new Error('Invalid retained receipt');
  return request;
}
