import { describe, expect, it } from 'vitest';
import {
  type CaptureSaveIntent,
  captureReceipt,
  restoreCaptureSave
} from './captureSaveState';

const intent: CaptureSaveIntent = {
  command: 'closeout_capture',
  url: '/api/tasks/work-orders/73/closeout/captures/',
  payload: {
    expected_version: 7,
    idempotency_key: 'same-operation',
    narrative: 'Retained story'
  },
  draft: { Narrative: 'Retained story' }
};
describe('capture save recovery', () => {
  it('reloads pending as uncertain and retains exact original intent', () => {
    expect(
      restoreCaptureSave(JSON.stringify({ status: 'pending', intent }), 73)
    ).toEqual({ status: 'uncertain', intent });
  });
  it('rejects a foreign Work command', () => {
    expect(
      restoreCaptureSave(JSON.stringify({ status: 'stale', intent }), 74)
    ).toBeNull();
  });
  it('rejects malformed stored drafts and missing operation identity', () => {
    for (const value of [
      null,
      { ...intent, draft: { Narrative: 1 } },
      { ...intent, payload: {} },
      { ...intent, command: 'complete_work_order' }
    ]) {
      expect(
        restoreCaptureSave(
          JSON.stringify({ status: 'uncertain', intent: value }),
          73
        )
      ).toBeNull();
    }
    expect(restoreCaptureSave('invalid JSON', 73)).toBeNull();
  });
  it('accepts only the matching durable capture command receipt', () => {
    const receipt = {
      work_order_id: 73,
      command: 'closeout_capture',
      idempotency_key: 'same-operation',
      lifecycle_version: 7,
      event_id: 11,
      metadata: { capture_id: 12 }
    };
    expect(captureReceipt(receipt, intent, 73)).toBe(true);
    for (const patch of [
      { work_order_id: 74 },
      { command: 'complete_work_order' },
      { idempotency_key: 'new-operation' },
      { lifecycle_version: 8 },
      { metadata: {} },
      { event_id: 0 }
    ])
      expect(captureReceipt({ ...receipt, ...patch }, intent, 73)).toBe(false);
  });
});
