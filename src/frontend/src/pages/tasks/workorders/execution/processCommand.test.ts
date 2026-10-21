import { describe, expect, it } from 'vitest';
import {
  type ProcessIntent,
  canReviewProcess,
  processReceipt,
  restoreProcess
} from './processCommand';
const intent: ProcessIntent = {
  kind: 'escalation',
  workId: 73,
  targetId: 401,
  url: '/api/kanban/work-orders/73/investigation-requests/401/',
  payload: {
    operation: 'escalate',
    idempotency_key: 'expert-original',
    expected_version: 7,
    request_version: 3,
    fields: {
      owner_id: 9,
      next_checkpoint: '2026-10-21T10:00:00Z',
      blocked_reason: 'Contradiction'
    }
  }
};
const receipt = {
  work_order_id: 73,
  request_id: 401,
  operation: 'escalate',
  idempotency_key: 'expert-original',
  expected_version: 7,
  version_before: 3,
  version_after: 4,
  request: { id: 401, work_order: 73 }
};
describe('expert/recurrence command integrity', () => {
  it('review cannot discard a newer uncertain or terminal operation', () => {
    const shown = { status: 'refused' as const, intent };
    expect(canReviewProcess(shown, shown)).toBe(true);
    for (const status of [
      'pending',
      'uncertain',
      'confirmed',
      'refused'
    ] as const) {
      const newer = {
        status,
        intent: {
          ...intent,
          payload: { ...intent.payload, idempotency_key: 'newer-original-key' }
        }
      };
      expect(canReviewProcess(newer, shown)).toBe(false);
      expect(canReviewProcess(newer, { ...shown, status: 'confirmed' })).toBe(
        false
      );
    }
    for (const status of ['pending', 'uncertain'] as const)
      expect(canReviewProcess({ ...shown, status }, shown)).toBe(false);
  });
  it('review requires the same command, Work and target even with the same key', () => {
    const shown = { status: 'confirmed' as const, intent };
    for (const changed of [
      { ...intent, kind: 'recurrence' as const },
      { ...intent, workId: 74 },
      { ...intent, targetId: 402 }
    ])
      expect(canReviewProcess({ ...shown, intent: changed }, shown)).toBe(
        false
      );
    expect(canReviewProcess({ status: 'idle' }, shown)).toBe(false);
    expect(canReviewProcess(shown, { status: 'idle' })).toBe(false);
  });
  it('requires exact expert request, parent, key and version receipt', () => {
    expect(processReceipt(receipt, intent)).toBe(true);
    for (const change of [
      { request_id: 402 },
      { work_order_id: 74 },
      { idempotency_key: 'other' },
      { operation: 'revise' },
      { version_after: 5 },
      { request: { id: 401, work_order: 74 } }
    ])
      expect(processReceipt({ ...receipt, ...change }, intent)).toBe(false);
  });
  it('keeps original owner/checkpoint/key across reload and rejects retargeted storage', () => {
    expect(
      restoreProcess(
        JSON.stringify({ status: 'pending', intent }),
        'escalation',
        73,
        401
      )
    ).toEqual({ status: 'uncertain', intent });
    for (const raw of [
      '{',
      '{}',
      JSON.stringify({
        status: 'uncertain',
        intent: { ...intent, targetId: 402 }
      }),
      JSON.stringify({
        status: 'pending',
        intent: { ...intent, url: '/api/other/' }
      })
    ])
      expect(restoreProcess(raw, 'escalation', 73, 401).status).toBe('refused');
  });
  it('accepts only the original linked/reopened recurrence result', () => {
    const recurrence: ProcessIntent = {
      kind: 'recurrence',
      workId: 73,
      targetId: 701,
      url: '/api/kanban/work-orders/commands/create/',
      payload: { idempotency_key: 'recurrence-original', prior_version: 7 }
    };
    const value = {
      id: 80,
      process_receipt: {
        command: 'create',
        work_order_id: 80,
        event_id: 91,
        idempotency_key: 'recurrence-original',
        lifecycle_version: 1,
        metadata: { recurrence_of: 73, occurrence_id: 701 }
      }
    };
    expect(processReceipt(value, recurrence)).toBe(true);
    expect(
      processReceipt(
        {
          ...value,
          process_receipt: { ...value.process_receipt, event_id: 0 }
        },
        recurrence
      )
    ).toBe(false);
    expect(
      processReceipt(
        {
          ...value,
          process_receipt: {
            ...value.process_receipt,
            metadata: { recurrence_of: 74, occurrence_id: 701 }
          }
        },
        recurrence
      )
    ).toBe(false);
    expect(
      processReceipt(
        {
          id: 73,
          process_receipt: {
            ...value.process_receipt,
            work_order_id: 73,
            lifecycle_version: 8,
            metadata: { occurrence_id: 701 }
          }
        },
        recurrence
      )
    ).toBe(true);
  });
});

describe('routine finding intent integrity', () => {
  const intent: ProcessIntent = {
    kind: 'routine_finding',
    workId: 73,
    targetId: 73,
    url: '/api/kanban/work-orders/commands/create/',
    payload: {
      idempotency_key: 'original-routine',
      machine: 18,
      recurrence_of: 73,
      prior_version: 7,
      corrective_origin_kind: 'routine_finding',
      work_order_type: 'corrective',
      title: 'Inspect leak',
      corrective_finding: 'Observed drip',
      corrective_reading: 701,
      corrective_step: 901
    }
  };
  const receipt = {
    id: 80,
    process_receipt: {
      command: 'create',
      work_order_id: 80,
      event_id: 911,
      idempotency_key: 'original-routine',
      lifecycle_version: 1,
      metadata: {
        origin_kind: 'routine_finding',
        outcome: 'linked_created',
        routine_work_id: 73,
        prior_version: 7
      }
    }
  };
  it('accepts only the matching new corrective receipt', () => {
    expect(processReceipt(receipt, intent)).toBe(true);
    expect(processReceipt({ ...receipt, id: 81 }, intent)).toBe(false);
    expect(processReceipt(receipt, { ...intent, workId: 74 })).toBe(false);
    expect(
      processReceipt(receipt, {
        ...intent,
        payload: { ...intent.payload, prior_version: 8 }
      })
    ).toBe(false);
    expect(
      processReceipt(receipt, {
        ...intent,
        payload: { ...intent.payload, idempotency_key: 'other' }
      })
    ).toBe(false);
  });
  it('restores pending as uncertain and preserves the original intent', () => {
    expect(
      restoreProcess(
        JSON.stringify({ status: 'pending', intent }),
        'routine_finding',
        73,
        73
      )
    ).toEqual({ status: 'uncertain', intent });
  });
  it('refuses mixed recurrence, invalid source, or alternate endpoint on restore', () => {
    for (const change of [
      { recurrence_occurrence: 701 },
      { corrective_origin_kind: 'recurrence' },
      { corrective_finding: '' },
      { recurrence_of: 74 },
      { corrective_step: true }
    ]) {
      expect(
        restoreProcess(
          JSON.stringify({
            status: 'uncertain',
            intent: { ...intent, payload: { ...intent.payload, ...change } }
          }),
          'routine_finding',
          73,
          73
        ).status
      ).toBe('refused');
    }
    expect(
      restoreProcess(
        JSON.stringify({
          status: 'uncertain',
          intent: { ...intent, url: '/other' }
        }),
        'routine_finding',
        73,
        73
      ).status
    ).toBe('refused');
  });
});
