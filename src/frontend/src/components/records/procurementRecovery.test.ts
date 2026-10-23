import { describe, expect, it } from 'vitest';
import {
  canonicalIntent,
  isConsumedAllocation,
  isReceiptResponse,
  readProcurementIntent
} from './procurementRecovery';

describe('native procurement recovery contracts', () => {
  it('accepts the native stock array without requiring a Work version', () => {
    expect(isReceiptResponse([{ pk: 11, batch: 'FIXTURE' }])).toBe(true);
    for (const value of [
      [],
      {},
      { version: 1 },
      [{ pk: 11 }, { pk: 11 }],
      [{ pk: 0 }],
      [{ pk: '11' }]
    ])
      expect(isReceiptResponse(value)).toBe(false);
  });
  it('never adopts malformed, other-family or other-scope retained intent', () => {
    const intent = {
      schema: 1,
      kind: 'receipt',
      scope: 'work:1',
      request: { maintenance_operation_key: 'original' }
    };
    expect(readProcurementIntent(null, 'receipt', 'work:1')).toBeNull();
    expect(
      readProcurementIntent(JSON.stringify(intent), 'receipt', 'work:1')
    ).toEqual(intent);
    for (const raw of [
      '{',
      '{}',
      'null',
      JSON.stringify({ ...intent, kind: 'document' }),
      JSON.stringify({ ...intent, scope: 'work:2' })
    ])
      expect(() => readProcurementIntent(raw, 'receipt', 'work:1')).toThrow();
  });
  it('compares JSON requests independent of field order while preserving array order and decimal strings', () => {
    expect(canonicalIntent({ b: 2, a: ['1', '2'] })).toBe(
      canonicalIntent({ a: ['1', '2'], b: 2 })
    );
    expect(canonicalIntent({ a: ['1', '2'] })).not.toBe(
      canonicalIntent({ a: ['2', '1'] })
    );
    expect(canonicalIntent({ quantity: '1.00' })).not.toBe(
      canonicalIntent({ quantity: 1 })
    );
  });
  it('retains the actual JSON transport snapshot including optional fields and dates', () => {
    const body = {
      batch: 'FIXTURE',
      expiry: undefined,
      source_at: new Date('2026-10-23T00:00:00Z')
    };
    const retained = canonicalIntent(body);
    body.batch = 'EDITED';
    expect(JSON.parse(retained)).toEqual({
      batch: 'FIXTURE',
      source_at: '2026-10-23T00:00:00.000Z'
    });
  });
  it('requires exact allocation identity and real stock-tracking to recover use', () => {
    const row = {
      id: 13,
      stock_item: 4,
      status: 'consumed',
      stock_tracking_id: 26
    };
    expect(isConsumedAllocation(row, 13)).toBe(true);
    expect(isConsumedAllocation(row, 14)).toBe(false);
    expect(isConsumedAllocation({ ...row, stock_tracking_id: null }, 13)).toBe(
      false
    );
    expect(isConsumedAllocation({ ...row, status: 'issued' }, 13)).toBe(false);
    expect(isConsumedAllocation({ hidden: true }, 13)).toBe(false);
  });
});
