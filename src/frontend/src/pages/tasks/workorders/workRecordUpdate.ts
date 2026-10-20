/** One immutable completion intent; retry/reconciliation always reuses it. */
export interface UpdateIntent {
  workOrderId: number;
  payload: {
    expected_version: number;
    idempotency_key: string;
    action: string;
    result: string;
    verification_summary: string;
    cause: string;
    follow_up: string;
    follow_up_required: boolean;
    downtime_minutes?: number;
    capture_id?: number;
  };
}
export interface UpdateReceipt {
  work_order_id: number;
  command: string;
  idempotency_key: string;
  event_id: number;
  lifecycle_status: string;
  lifecycle_version: number;
  metadata: { closeout_id: number };
}
export type UpdateState =
  | { status: 'idle' }
  | {
      status: 'pending' | 'uncertain' | 'failed' | 'stale';
      intent: UpdateIntent;
      message?: string;
      reconciling?: boolean;
    }
  | {
      status: 'confirmed';
      intent: UpdateIntent;
      receipt: UpdateReceipt;
      recordedAt: string | null;
    };

/** Queued, unrelated or malformed responses must never look like success. */
export function matchingReceipt(
  value: unknown,
  intent: UpdateIntent
): value is UpdateReceipt {
  if (!value || typeof value !== 'object') return false;
  const row = value as UpdateReceipt;
  return (
    row.work_order_id === intent.workOrderId &&
    row.command === 'complete_work_order' &&
    row.idempotency_key === intent.payload.idempotency_key &&
    row.lifecycle_status === 'completed' &&
    row.lifecycle_version === intent.payload.expected_version + 1 &&
    Number.isSafeInteger(row.event_id) &&
    row.event_id > 0 &&
    Number.isSafeInteger(row.metadata?.closeout_id) &&
    row.metadata.closeout_id > 0
  );
}

/** A reload of an in-flight operation starts uncertain, never ready to resubmit. */
export function restoreUpdate(
  raw: string | null,
  workOrderId: number
): UpdateState {
  if (!raw) return { status: 'idle' };
  try {
    const row = JSON.parse(raw) as UpdateState;
    if (row.status === 'idle') return row;
    if (
      !['pending', 'uncertain', 'failed', 'stale', 'confirmed'].includes(
        row.status
      )
    )
      return { status: 'idle' };
    const intent = 'intent' in row ? row.intent : null;
    if (
      !intent ||
      intent.workOrderId !== workOrderId ||
      !Number.isSafeInteger(intent.payload?.expected_version) ||
      intent.payload.expected_version < 1 ||
      typeof intent.payload.idempotency_key !== 'string' ||
      !intent.payload.idempotency_key ||
      !['action', 'result', 'verification_summary', 'cause', 'follow_up'].every(
        (key) =>
          typeof intent.payload[key as keyof typeof intent.payload] === 'string'
      ) ||
      typeof intent.payload.follow_up_required !== 'boolean'
    )
      return { status: 'idle' };
    if (row.status === 'confirmed' && !matchingReceipt(row.receipt, intent))
      return { status: 'uncertain', intent };
    return row.status === 'pending' ? { status: 'uncertain', intent } : row;
  } catch {
    return { status: 'idle' };
  }
}

export function updateFailure(
  status?: number,
  code?: string
): 'failed' | 'stale' | 'uncertain' {
  if (code === 'STALE_VERSION') return 'stale';
  if (code === 'IDEMPOTENCY_CONFLICT') return 'uncertain';
  // Transports, gateways and unknown responses cannot prove a rollback.
  return status != null &&
    status >= 400 &&
    status < 500 &&
    status !== 404 &&
    status !== 408 &&
    status !== 429
    ? 'failed'
    : 'uncertain';
}
