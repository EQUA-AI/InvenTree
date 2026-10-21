export interface ContinuityIntent {
  workOrderId: number;
  action: 'hold' | 'resume' | 'accept_handover';
  handoverId?: number;
  fromStatus?: string;
  payload: {
    expected_version: number;
    idempotency_key: string;
    reason: string;
  };
}

export interface ContinuityReceipt {
  work_order_id: number;
  command: 'hold' | 'resume' | 'accept_handover';
  idempotency_key: string;
  event_id: number;
  lifecycle_status: string;
  metadata?: { handover_id?: number };
  lifecycle_version: number;
}

export type ContinuityCommandState =
  | { status: 'idle' | 'blocked' }
  | {
      status: 'pending' | 'uncertain' | 'failed' | 'stale';
      intent: ContinuityIntent;
      message?: string;
    }
  | {
      status: 'confirmed';
      intent: ContinuityIntent;
      receipt: ContinuityReceipt;
    };

/** A status string or unrelated receipt is never confirmation of this command. */
export function matchingContinuityReceipt(
  value: unknown,
  intent: ContinuityIntent
): value is ContinuityReceipt {
  if (!value || typeof value !== 'object') return false;
  const row = value as ContinuityReceipt;
  return (
    row.work_order_id === intent.workOrderId &&
    row.command === intent.action &&
    row.idempotency_key === intent.payload.idempotency_key &&
    row.lifecycle_version === intent.payload.expected_version + 1 &&
    row.lifecycle_status ===
      (intent.action === 'accept_handover'
        ? intent.fromStatus
        : intent.action === 'hold'
          ? 'on_hold'
          : 'in_progress') &&
    (intent.action !== 'accept_handover' ||
      (row.metadata?.handover_id === intent.handoverId &&
        Number.isSafeInteger(intent.handoverId) &&
        intent.handoverId! > 0)) &&
    Number.isSafeInteger(row.event_id) &&
    row.event_id > 0
  );
}

/** A refresh keeps the original operation; unreadable retained intent fails closed. */
export function restoreContinuityCommand(
  raw: string | null,
  workOrderId: number
): ContinuityCommandState {
  if (raw === null) return { status: 'idle' };
  try {
    const row = JSON.parse(raw) as ContinuityCommandState;
    if (!row || !('intent' in row)) return { status: 'blocked' };
    if (
      'message' in row &&
      row.message !== undefined &&
      typeof row.message !== 'string'
    )
      return { status: 'blocked' };
    const intent = row.intent;
    if (
      !intent ||
      intent.workOrderId !== workOrderId ||
      !['hold', 'resume', 'accept_handover'].includes(intent.action) ||
      !Number.isSafeInteger(intent.payload?.expected_version) ||
      intent.payload.expected_version < 1 ||
      typeof intent.payload.idempotency_key !== 'string' ||
      !intent.payload.idempotency_key.trim() ||
      typeof intent.payload.reason !== 'string' ||
      (intent.action === 'hold' && !intent.payload.reason.trim()) ||
      (intent.action === 'accept_handover' &&
        (!Number.isSafeInteger(intent.handoverId) ||
          intent.handoverId! < 1 ||
          ![
            'draft',
            'planned',
            'ready',
            'in_progress',
            'on_hold',
            'verifying',
            'completed',
            'canceled'
          ].includes(intent.fromStatus ?? '')))
    )
      return { status: 'blocked' };
    if (row.status === 'confirmed')
      return matchingContinuityReceipt(row.receipt, intent)
        ? row
        : { status: 'uncertain', intent };
    if (row.status === 'pending') return { status: 'uncertain', intent };
    return ['uncertain', 'failed', 'stale'].includes(row.status)
      ? row
      : { status: 'blocked' };
  } catch {
    return { status: 'blocked' };
  }
}
