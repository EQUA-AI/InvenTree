import { describe, expect, it } from 'vitest';
import { materials } from '../../../tests/r8/procurementFixtures';
import {
  approvedLine,
  commandResult,
  placementPayload,
  selectedOffer
} from './procurement';

const line = () => materials('selected').procurement!.lines[0];
describe('procurement authority and identity', () => {
  it('maps exact supplier part and freight once, with no fixture values added', () => {
    const value = placementPayload(line(), Date.parse('2026-10-23T00:00:00Z'))!;
    expect(value.line_items).toEqual([
      { supplier_part_id: 61, quantity: '1', unit_price: '620.00' }
    ]);
    expect(value.extra_lines).toEqual([
      { description: 'Freight', quantity: '1', unit_price: '45.00' }
    ]);
    expect(value.total).toBe('665.00');
  });
  it.each([
    'stale',
    'unresolved',
    'expired',
    'late',
    'unknown',
    'discount',
    'estimated',
    'no-deadline'
  ])('refuses automatic purchase preparation: %s', (kind) => {
    const value = line();
    const offer = selectedOffer(value)!;
    if (kind === 'stale') value.selection_current = false;
    if (kind === 'unresolved') offer.technical_eligibility = 'unresolved';
    if (kind === 'expired') offer.valid_through = '2020-01-01T00:00:00Z';
    if (kind === 'late') offer.promised_at = '2026-10-28T00:00:00Z';
    if (kind === 'unknown') offer.tax = null;
    if (kind === 'discount') offer.discount = '5';
    if (kind === 'estimated') offer.promise_basis = 'estimate';
    if (kind === 'no-deadline') value.needed_at = null;
    expect(placementPayload(value)).toBeNull();
  });
  it('does not interpret a generic version envelope or a different event as a command receipt', () => {
    expect(
      commandResult('source', { operation_key: 'same' }, { version: 1 })
    ).toBe(false);
    expect(
      commandResult(
        'ack',
        { operation_key: 'same' },
        {
          operation_key: 'same',
          event_id: 1,
          event_type: 'PROC_SOURCE_SELECTED'
        }
      )
    ).toBe(false);
    expect(
      commandResult(
        'ack',
        { operation_key: 'same' },
        {
          operation_key: 'same',
          event_id: 1,
          event_type: 'PROC_PO_ACKNOWLEDGED'
        }
      )
    ).toBe(true);
    expect(
      commandResult(
        'document',
        { operation_key: 'same' },
        {
          operation_key: 'different',
          pk: 1,
          state: 'recorded_not_sent',
          supplier_email_sent: false
        }
      )
    ).toBe(false);
  });
  it('only links one exact native line from an actual successful result payload', () => {
    expect(approvedLine({ line_ids: [81] })).toBe(81);
    expect(approvedLine({ payload: { line_ids: [81] } })).toBe(81);
    expect(approvedLine({ line_ids: [81, 82] })).toBeNull();
    expect(approvedLine({ line_ids: ['81'] })).toBeNull();
    expect(approvedLine({ order_id: 51 })).toBeNull();
  });
});
