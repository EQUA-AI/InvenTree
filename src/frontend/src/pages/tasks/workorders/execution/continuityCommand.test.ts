import { describe, expect, it } from 'vitest';
import {
  type ContinuityIntent,
  matchingContinuityReceipt,
  restoreContinuityCommand
} from './continuityCommand';

const intent: ContinuityIntent = {
  workOrderId: 73,
  action: 'hold',
  payload: {
    expected_version: 7,
    idempotency_key: 'original-hold',
    reason: 'Recorded hold reason'
  }
};
const receipt = {
  work_order_id: 73,
  command: 'hold',
  idempotency_key: 'original-hold',
  event_id: 41,
  lifecycle_status: 'on_hold',
  lifecycle_version: 8
};

describe('hold/resume record command integrity', () => {
  it('requires the exact command, work, key, next version, state and event', () => {
    expect(matchingContinuityReceipt(receipt, intent)).toBe(true);
    for (const change of [
      { work_order_id: 74 },
      { command: 'resume' },
      { idempotency_key: 'other' },
      { lifecycle_version: 9 },
      { lifecycle_status: 'in_progress' },
      { event_id: 0 },
      { event_id: '41' }
    ])
      expect(matchingContinuityReceipt({ ...receipt, ...change }, intent)).toBe(
        false
      );
    const resume: ContinuityIntent = { ...intent, action: 'resume' };
    expect(
      matchingContinuityReceipt(
        { ...receipt, command: 'resume', lifecycle_status: 'in_progress' },
        resume
      )
    ).toBe(true);
  });
  it('restores an in-flight operation as uncertain with the identical key/version/reason', () => {
    expect(
      restoreContinuityCommand(
        JSON.stringify({ status: 'pending', intent }),
        73
      )
    ).toEqual({ status: 'uncertain', intent });
    expect(
      restoreContinuityCommand(
        JSON.stringify({
          status: 'confirmed',
          intent,
          receipt: { ...receipt, command: 'resume' }
        }),
        73
      )
    ).toEqual({ status: 'uncertain', intent });
  });
  it('blocks unreadable, retargeted or malformed retained intent rather than creating a new one', () => {
    for (const raw of [
      '',
      '{',
      '{}',
      JSON.stringify({
        status: 'uncertain',
        intent,
        message: { invalid: true }
      }),
      JSON.stringify({
        status: 'pending',
        intent: { ...intent, workOrderId: 74 }
      }),
      JSON.stringify({
        status: 'pending',
        intent: {
          ...intent,
          payload: { ...intent.payload, expected_version: true }
        }
      })
    ])
      expect(restoreContinuityCommand(raw, 73)).toEqual({ status: 'blocked' });
    expect(restoreContinuityCommand(null, 73)).toEqual({ status: 'idle' });
  });
  it('retains a matching saved receipt across reload', () => {
    const result = restoreContinuityCommand(
      JSON.stringify({ status: 'confirmed', intent, receipt }),
      73
    );
    expect(result).toEqual({ status: 'confirmed', intent, receipt });
  });
});

describe('explicit incoming handover receipt', () => {
  const handover: ContinuityIntent = {
    ...intent,
    action: 'accept_handover',
    fromStatus: 'on_hold',
    handoverId: 19
  };
  const saved = {
    ...receipt,
    command: 'accept_handover',
    metadata: { handover_id: 19 }
  };
  it('requires the exact handover identity and unchanged lifecycle status', () => {
    expect(matchingContinuityReceipt(saved, handover)).toBe(true);
    expect(
      matchingContinuityReceipt(
        { ...saved, metadata: { handover_id: 20 } },
        handover
      )
    ).toBe(false);
    expect(
      matchingContinuityReceipt(
        { ...saved, lifecycle_status: 'in_progress' },
        handover
      )
    ).toBe(false);
    expect(
      matchingContinuityReceipt({ ...saved, metadata: {} }, handover)
    ).toBe(false);
  });
  it('reload retains the original acceptance intent as uncertain', () => {
    expect(
      restoreContinuityCommand(
        JSON.stringify({ status: 'pending', intent: handover }),
        73
      )
    ).toEqual({ status: 'uncertain', intent: handover });
  });
});
