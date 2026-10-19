import type { MachineAnomaly } from '@lib/types/MachineHealth';
import { describe, expect, it } from 'vitest';
import { anomaliesForSignal } from './signalContext';

const alarm = (pk: number, bindings: number[]): MachineAnomaly =>
  ({
    pk,
    status: 'open',
    signals: bindings.map((binding_id) => ({ binding_id }))
  }) as MachineAnomaly;

describe('canonical signal association', () => {
  it('cites binding ids rather than matching display names', () => {
    const rows = [alarm(1, [201]), alarm(2, [202])];
    expect(anomaliesForSignal(rows, 201)).toEqual({
      cited: [rows[0]],
      unassociated: []
    });
  });
  it('keeps missing association separate from a known absence', () => {
    const rows = [alarm(1, [])];
    expect(anomaliesForSignal(rows, 201)).toEqual({
      cited: [],
      unassociated: rows
    });
  });
  it('does not duplicate an anomaly with multiple citations to a binding', () => {
    expect(anomaliesForSignal([alarm(1, [201, 201])], 201).cited).toHaveLength(
      1
    );
  });
  it('resolved and suppressed records do not become active row anomalies', () => {
    const rows = [
      alarm(1, [201]),
      { ...alarm(2, [201]), status: 'resolved' as const },
      { ...alarm(3, []), status: 'suppressed' as const }
    ];
    expect(anomaliesForSignal(rows, 201)).toEqual({
      cited: [rows[0]],
      unassociated: []
    });
  });
  it('a known different binding is not an unassociated anomaly', () => {
    expect(anomaliesForSignal([alarm(1, [202])], 201)).toEqual({
      cited: [],
      unassociated: []
    });
  });
});
