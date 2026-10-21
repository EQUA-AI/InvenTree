import { describe, expect, it } from 'vitest';

import {
  type MaterialReadout,
  type MaterialRequirement,
  type MaterialReservation,
  coveredByStock,
  fitSourceLabel,
  reservationStages,
  reservedQuantity
} from './materials';

const reservation = (
  overrides: Partial<MaterialReservation> = {}
): MaterialReservation => ({
  id: 1,
  quantity: 2,
  status: 'reserved',
  stock_item: { pk: 9, hidden: false, location: 'Store A' },
  reserved: { by: 'planner', at: '2026-10-19T08:00:00Z' },
  staged: null,
  issued: null,
  closed: null,
  usage: [],
  ...overrides
});

const requirement = (
  overrides: Partial<MaterialRequirement> = {}
): MaterialRequirement => ({
  key: 'k',
  part: { pk: 5, name: 'Seal', ipn: '', units: 'ea', virtual: false },
  requested_part: null,
  quantity: 2,
  required: true,
  fit_basis: { source: 'procedure', substitution_policy: 'none', note: '' },
  reservations: [reservation()],
  shortages: [],
  availability_check: null,
  ...overrides
});

const readout = (requirements: MaterialRequirement[]): MaterialReadout => ({
  work_order: 73,
  source: 'job_kit',
  kits_enabled: true,
  stock_visible: true,
  component: null,
  requirements,
  unmatched_usage: []
});

describe('material stages', () => {
  it('a reservation alone is reserved, with issue and use still to come', () => {
    expect(
      reservationStages(reservation()).map((s) => [s.key, s.state])
    ).toEqual([
      ['reserved', 'done'],
      ['issued', 'pending'],
      ['used', 'pending']
    ]);
  });

  it('an issue keeps its unknown recorder unknown', () => {
    const [, issued] = reservationStages(
      reservation({
        status: 'issued',
        issued: { by: null, at: '2026-10-19T09:00:00Z' }
      })
    );
    expect(issued).toEqual({
      key: 'issued',
      state: 'done',
      by: null,
      at: '2026-10-19T09:00:00Z'
    });
  });

  it('use straight from reservation marks the issue as not recorded, not done', () => {
    const stages = reservationStages(
      reservation({
        status: 'consumed',
        closed: { status: 'consumed', by: 'tech', at: 't', tracking_id: 4 }
      })
    );
    expect(stages.map((s) => [s.key, s.state, s.by])).toEqual([
      ['reserved', 'done', 'planner'],
      ['issued', 'skipped', null],
      ['used', 'done', 'tech']
    ]);
  });

  it('a return or release ends the sequence in its own words', () => {
    expect(
      reservationStages(
        reservation({
          status: 'returned',
          issued: { by: null, at: 'i' },
          closed: { status: 'returned', by: null, at: 'r', tracking_id: null }
        })
      ).map((s) => s.key)
    ).toEqual(['reserved', 'issued', 'returned']);
    expect(
      reservationStages(
        reservation({
          status: 'released',
          closed: { status: 'released', by: null, at: 'r', tracking_id: null }
        })
      ).map((s) => s.key)
    ).toEqual(['reserved', 'released']);
  });
});

describe('stock coverage', () => {
  it('counts live and used reservations, not released ones', () => {
    expect(
      reservedQuantity(
        requirement({
          reservations: [
            reservation({ quantity: 1 }),
            reservation({ id: 2, quantity: 1, status: 'consumed' }),
            reservation({ id: 3, quantity: 5, status: 'released' })
          ]
        })
      )
    ).toBe(2);
    expect(reservedQuantity(requirement({ reservations: null }))).toBeNull();
  });

  it('says stock covered the job only when every required line is reserved and nothing is short', () => {
    expect(coveredByStock(readout([requirement()]))).toBe(true);
    expect(
      coveredByStock(
        readout([requirement({ reservations: [reservation({ quantity: 1 })] })])
      )
    ).toBe(false);
    expect(
      coveredByStock(
        readout([
          requirement({
            shortages: [
              {
                id: 1,
                quantity: 1,
                status: 'ordered',
                purchase_order_line: null,
                reason: ''
              }
            ]
          })
        ])
      )
    ).toBe(false);
    // An optional line does not hold the job up.
    expect(
      coveredByStock(
        readout([
          requirement(),
          requirement({ key: 'o', required: false, reservations: [] })
        ])
      )
    ).toBe(true);
    // Without a kit nothing is reserved, so nothing is claimed.
    expect(
      coveredByStock({ ...readout([requirement()]), source: 'requirements' })
    ).toBe(false);
    expect(coveredByStock(readout([]))).toBe(false);
  });
});

it('labels native fit sources and preserves an unfamiliar source verbatim', () => {
  const labels = {
    manual: 'Manually recorded requirement',
    procedure: 'Procedure requirement'
  };
  expect(fitSourceLabel('manual', labels)).toBe(labels.manual);
  expect(fitSourceLabel('procedure', labels)).toBe(labels.procedure);
  expect(fitSourceLabel('site-specific', labels)).toBe('site-specific');
});
