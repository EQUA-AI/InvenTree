import { describe, expect, it } from 'vitest';

import { type MachinePositions, positionSummary } from './machinePositions';

const data: MachinePositions = {
  rule: 'reference_positions.v1',
  machine: 18,
  machine_work: { count: 3 },
  positions: [
    {
      id: 'machine',
      family: null,
      family_name: null,
      components: [],
      signals: [],
      work: null
    },
    {
      id: 'bearing',
      family: 'MOTOR-DE-BRG',
      family_name: 'Motor Drive-End Bearing',
      components: [
        {
          code: 'PS-MOTOR-DE-BRG:1',
          name: 'Motor Drive-End Bearing',
          status: 'draft',
          provenance: 'Inferred',
          part: {
            pk: 1,
            name: 'Motor Drive-End Bearing',
            IPN: 'PS-MOTOR-DE-BRG'
          }
        }
      ],
      signals: [],
      work: { count: 1, results: [] }
    },
    {
      id: 'pump',
      family: 'CASING',
      family_name: 'Pump Casing and Spiral Case',
      components: [],
      signals: [],
      work: null
    },
    {
      id: 'seal',
      family: null,
      family_name: null,
      components: [],
      signals: [],
      work: null
    }
  ]
};

describe('position summary', () => {
  it('is nothing until the records have loaded', () => {
    expect(positionSummary(undefined, 'bearing')).toBeNull();
  });

  it('keeps machine work as context for the whole machine', () => {
    expect(positionSummary(data, 'machine')).toEqual({
      kind: 'machine',
      machineWork: 3
    });
  });

  it('separates a position with no family from a family with no slot', () => {
    expect(positionSummary(data, 'seal')).toEqual({
      kind: 'unbound',
      machineWork: 3
    });
    expect(positionSummary(data, 'pump')).toEqual({
      kind: 'no-slot',
      family: 'CASING',
      familyName: 'Pump Casing and Spiral Case',
      machineWork: 3
    });
    // A position the response does not carry is unbound, not empty-bound.
    expect(positionSummary(data, 'impeller')?.kind).toBe('unbound');
  });

  it("counts the rest of the machine's work as parent context", () => {
    const bound = positionSummary(data, 'bearing');
    expect(bound?.kind).toBe('bound');
    expect(bound?.kind === 'bound' && bound.parentWork).toBe(2);
  });

  it('keeps unknown work unknown', () => {
    const unknown = { ...data, machine_work: null };
    expect(positionSummary(unknown, 'machine')).toEqual({
      kind: 'machine',
      machineWork: null
    });
    const bound = positionSummary(unknown, 'bearing');
    expect(bound?.kind === 'bound' && bound.parentWork).toBeNull();
  });
});
