import { describe, expect, it } from 'vitest';
import {
  type UpdateIntent,
  matchingReceipt,
  restoreUpdate,
  updateFailure
} from './workRecordUpdate';
const intent: UpdateIntent = {
  workOrderId: 73,
  payload: {
    expected_version: 7,
    idempotency_key: 'original-key',
    action: 'Inspected seal',
    result: 'Observed flow',
    verification_summary: 'Recorded test',
    cause: '',
    follow_up: '',
    follow_up_required: false
  }
};
const receipt = {
  work_order_id: 73,
  command: 'complete_work_order',
  idempotency_key: 'original-key',
  event_id: 80,
  lifecycle_status: 'completed',
  lifecycle_version: 8,
  metadata: { closeout_id: 90 }
};
describe('one logical final update', () => {
  it('requires a matching committed receipt, never a queued or unrelated response', () => {
    expect(matchingReceipt(receipt, intent)).toBe(true);
    for (const changed of [
      { idempotency_key: 'another-key' },
      { work_order_id: 74 },
      { lifecycle_status: 'queued' },
      { lifecycle_version: 7 },
      { metadata: {} },
      { event_id: null },
      { command: 'transition' }
    ])
      expect(matchingReceipt({ ...receipt, ...changed }, intent)).toBe(false);
  });
  it('restores pending work as uncertain with the exact original key, payload and version', () => {
    expect(
      restoreUpdate(JSON.stringify({ status: 'pending', intent }), 73)
    ).toEqual({ status: 'uncertain', intent });
    expect(
      restoreUpdate(JSON.stringify({ status: 'uncertain', intent }), 73)
    ).toEqual({ status: 'uncertain', intent });
  });
  it('separates work identities and rejects malformed storage', () => {
    expect(
      restoreUpdate(JSON.stringify({ status: 'pending', intent }), 74)
    ).toEqual({ status: 'idle' });
    expect(restoreUpdate('{broken', 73)).toEqual({ status: 'idle' });
    expect(
      restoreUpdate(
        JSON.stringify({
          status: 'pending',
          intent: { ...intent, payload: { expected_version: 7 } }
        }),
        73
      )
    ).toEqual({ status: 'idle' });
  });
  it('retains confirmed state and refuses a forged success', () => {
    const confirmed = {
      status: 'confirmed',
      intent,
      receipt,
      recordedAt: null
    };
    expect(restoreUpdate(JSON.stringify(confirmed), 73)).toEqual(confirmed);
    expect(
      restoreUpdate(
        JSON.stringify({
          ...confirmed,
          receipt: { ...receipt, idempotency_key: 'other' }
        }),
        73
      ).status
    ).toBe('uncertain');
  });
  it('distinguishes rollback refusal, stale intent and uncertain transports', () => {
    expect(updateFailure(400, 'CLOSEOUT_INVALID')).toBe('failed');
    expect(updateFailure(409, 'STALE_VERSION')).toBe('stale');
    expect(updateFailure(409, 'IDEMPOTENCY_CONFLICT')).toBe('uncertain');
    for (const status of [undefined, 404, 408, 429, 500, 502, 504])
      expect(updateFailure(status)).toBe('uncertain');
  });
});
