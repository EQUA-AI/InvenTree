import type { Page } from '@playwright/test';

/**
 * Synthetic `reference_positions.v1` records for machine 18: a draft bearing
 * slot with a warning reading and one high job, a verified motor slot with a
 * normal reading and no work, a casing family with no slot, and the four
 * positions with no family. Three open jobs on the machine in all.
 */
export function positionsFixture(overrides: Record<string, unknown> = {}) {
  const slot = (ipn: string, name: string, status: 'draft' | 'verified') => ({
    code: `${ipn}:1`,
    name,
    status,
    provenance:
      status === 'draft'
        ? 'Inferred from source tags; physical assignment is unverified.'
        : 'Workbook population 4c1f00a2e9b1',
    part: { pk: ipn.length, name, IPN: ipn }
  });
  const reading = (id: number, name: string, value: number, state: string) => ({
    binding_id: id,
    display_name: name,
    unit: 'degC',
    value,
    observed_at: '2026-10-19T12:00:00Z',
    quality: 'good',
    stale: false,
    state
  });
  const none = { components: [], signals: [], work: null };
  return {
    rule: 'reference_positions.v1',
    machine: 18,
    machine_work: { count: 3 },
    positions: [
      { id: 'machine', family: null, family_name: null, ...none },
      {
        id: 'motor',
        family: 'MOTOR',
        family_name: 'Electric Motor',
        components: [slot('PS-MOTOR', 'Electric Motor', 'verified')],
        signals: [reading(201, 'Motor winding temperature', 70, 'normal')],
        work: { count: 0, results: [] }
      },
      {
        id: 'bearing',
        family: 'MOTOR-DE-BRG',
        family_name: 'Motor Drive-End Bearing',
        components: [
          slot('PS-MOTOR-DE-BRG', 'Motor Drive-End Bearing', 'draft')
        ],
        signals: [reading(202, 'DE bearing temperature', 95, 'warning')],
        work: {
          count: 1,
          results: [
            {
              id: 73,
              reference: 'WO-73',
              title: 'Replace the motor drive-end bearing',
              priority: 'high',
              lifecycle_status: 'planned',
              affected_component_ref: 'PS-MOTOR-DE-BRG:1',
              owner: 'Synthetic owner'
            }
          ]
        }
      },
      { id: 'coupling', family: null, family_name: null, ...none },
      { id: 'seal', family: null, family_name: null, ...none },
      { id: 'impeller', family: null, family_name: null, ...none },
      {
        id: 'pump',
        family: 'CASING',
        family_name: 'Pump Casing and Spiral Case',
        ...none
      },
      {
        id: 'discharge',
        family: 'DISCHARGE',
        family_name: 'Pump Discharge Line',
        ...none
      },
      { id: 'base', family: null, family_name: null, ...none }
    ],
    ...overrides
  };
}

export async function mockPositions(
  page: Page,
  options: { status?: number; data?: Record<string, unknown> } = {}
) {
  let reads = 0;
  await page.route('**/api/assets/machines/18/positions/', (route) => {
    reads += 1;
    return options.status && options.status !== 200
      ? route.fulfill({ status: options.status, json: { detail: 'Synthetic' } })
      : route.fulfill({ json: options.data ?? positionsFixture() });
  });
  return { reads: () => reads };
}
