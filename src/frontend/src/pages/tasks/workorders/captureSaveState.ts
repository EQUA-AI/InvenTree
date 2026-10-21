export interface CaptureSaveIntent {
  url: string;
  command: 'closeout_capture' | 'closeout_decisions';
  payload: {
    expected_version: number;
    idempotency_key: string;
    [key: string]: unknown;
  };
  draft: Record<string, string>;
}
export type CaptureSaveState = {
  status: 'failed' | 'stale' | 'uncertain' | 'pending';
  intent: CaptureSaveIntent;
} | null;

export function restoreCaptureSave(
  raw: string | null,
  workOrderId: number
): CaptureSaveState {
  try {
    const state = JSON.parse(raw || 'null') as CaptureSaveState;
    if (
      !state ||
      !['pending', 'uncertain', 'failed', 'stale'].includes(state.status)
    )
      return null;
    const { intent } = state;
    if (
      !intent ||
      !['closeout_capture', 'closeout_decisions'].includes(intent.command) ||
      typeof intent.url !== 'string' ||
      !intent.url.includes(`/work-orders/${workOrderId}/closeout/captures/`) ||
      !Number.isSafeInteger(intent.payload?.expected_version) ||
      intent.payload.expected_version < 1 ||
      typeof intent.payload.idempotency_key !== 'string' ||
      !intent.payload.idempotency_key ||
      !intent.draft ||
      !Object.values(intent.draft).every((value) => typeof value === 'string')
    )
      return null;
    return {
      ...state,
      status: state.status === 'pending' ? 'uncertain' : state.status
    };
  } catch {
    return null;
  }
}

export function captureReceipt(
  value: any,
  intent: CaptureSaveIntent,
  workOrderId: number
): boolean {
  return (
    value?.work_order_id === workOrderId &&
    value?.command === intent.command &&
    value?.idempotency_key === intent.payload.idempotency_key &&
    value?.lifecycle_version === intent.payload.expected_version &&
    Number.isSafeInteger(value?.event_id) &&
    value.event_id > 0 &&
    Number.isSafeInteger(value?.metadata?.capture_id) &&
    value.metadata.capture_id > 0
  );
}
