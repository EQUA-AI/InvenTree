export interface ProcessIntent {
  kind: 'escalation' | 'recurrence';
  workId: number;
  targetId: number;
  url: string;
  payload: Record<string, unknown> & { idempotency_key: string };
}

/** Only an exact durable receipt establishes this operation's result. */
export function processReceipt(value: unknown, intent: ProcessIntent): boolean {
  if (!value || typeof value !== 'object') return false;
  const data = value as Record<string, any>;
  const body = intent.payload;
  if (intent.kind === 'escalation')
    return (
      data.work_order_id === intent.workId &&
      data.request_id === intent.targetId &&
      data.operation === 'escalate' &&
      data.idempotency_key === body.idempotency_key &&
      data.expected_version === body.expected_version &&
      data.version_before === body.request_version &&
      data.version_after === Number(body.request_version) + 1 &&
      data.request?.id === intent.targetId &&
      data.request?.work_order === intent.workId
    );
  const receipt = data.process_receipt;
  return (
    receipt?.command === 'create' &&
    receipt.idempotency_key === body.idempotency_key &&
    receipt.metadata?.occurrence_id === intent.targetId &&
    Number.isSafeInteger(receipt.event_id) &&
    receipt.event_id > 0 &&
    data.id === receipt.work_order_id &&
    (receipt.metadata?.recurrence_of === intent.workId ||
      (receipt.work_order_id === intent.workId &&
        receipt.lifecycle_version === Number(body.prior_version) + 1))
  );
}

export type ProcessState =
  | { status: 'idle' }
  | {
      status: 'pending' | 'uncertain' | 'confirmed' | 'refused';
      intent: ProcessIntent;
      message?: string;
      confirmedVersion?: number;
      result?: {
        outcome: 'reopened' | 'linked_created';
        workId: number;
        reference?: string;
        acceptance?: string;
      };
    };

/** Review may release only the same terminal operation that this instance shows. */
export function canReviewProcess(stored: ProcessState, shown: ProcessState) {
  return (
    (stored.status === 'confirmed' || stored.status === 'refused') &&
    (shown.status === 'confirmed' || shown.status === 'refused') &&
    !!shown.intent.payload.idempotency_key &&
    stored.intent.kind === shown.intent.kind &&
    stored.intent.workId === shown.intent.workId &&
    stored.intent.targetId === shown.intent.targetId &&
    stored.intent.payload.idempotency_key ===
      shown.intent.payload.idempotency_key
  );
}

/** Retained operations can only address their original Work and command. */
export function restoreProcess(
  raw: string | null,
  kind: ProcessIntent['kind'],
  workId: number,
  targetId: number
): ProcessState {
  if (raw === null) return { status: 'idle' };
  try {
    const row = JSON.parse(raw) as ProcessState;
    if (!('intent' in row)) throw new Error();
    const i = row.intent;
    if (
      i.kind !== kind ||
      i.workId !== workId ||
      i.targetId !== targetId ||
      !i.payload ||
      typeof i.payload.idempotency_key !== 'string' ||
      !i.payload.idempotency_key.trim()
    )
      throw new Error();
    const positive = (value: unknown) =>
      Number.isSafeInteger(value) && Number(value) > 0;
    if (kind === 'escalation') {
      const fields = i.payload.fields as Record<string, unknown> | undefined;
      if (
        i.payload.operation !== 'escalate' ||
        !positive(i.payload.expected_version) ||
        !positive(i.payload.request_version) ||
        !fields ||
        !positive(fields.owner_id) ||
        typeof fields.next_checkpoint !== 'string' ||
        !Number.isFinite(Date.parse(fields.next_checkpoint)) ||
        typeof fields.blocked_reason !== 'string' ||
        !fields.blocked_reason.trim()
      )
        throw new Error();
    } else if (
      i.payload.recurrence_of !== workId ||
      i.payload.recurrence_occurrence !== targetId ||
      !positive(i.payload.machine) ||
      !positive(i.payload.prior_version)
    )
      throw new Error();
    const url =
      kind === 'escalation'
        ? `/api/kanban/work-orders/${workId}/investigation-requests/${targetId}/`
        : '/api/kanban/work-orders/commands/create/';
    if (
      i.url !== url ||
      !['pending', 'uncertain', 'confirmed', 'refused'].includes(row.status)
    )
      throw new Error();
    return row.status === 'pending' ? { ...row, status: 'uncertain' } : row;
  } catch {
    return {
      status: 'refused',
      intent: {
        kind,
        workId,
        targetId,
        url: '',
        payload: { idempotency_key: '' }
      },
      message: 'Retained operation unreadable. Review the saved Work history.'
    };
  }
}
