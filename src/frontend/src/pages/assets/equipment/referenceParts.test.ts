import { describe, expect, it } from 'vitest';

import {
  REFERENCE_PARTS,
  referenceDepth,
  referenceListOrder
} from './referenceParts';

describe('reference model positions', () => {
  it('mirror the scene PARTS exactly, by stable ID', async () => {
    const { PARTS } = await import('./twinScene.mjs');
    expect(
      PARTS.map(({ id, number, parent }) => ({ id, number, parent }))
    ).toEqual(REFERENCE_PARTS);
    expect(PARTS.every((part) => part.conceptual)).toBe(true);
  });

  it('lists every position once in numeric marker order', () => {
    expect(referenceListOrder()).toEqual([
      'machine',
      'motor',
      'bearing',
      'coupling',
      'seal',
      'impeller',
      'pump',
      'discharge',
      'base'
    ]);
    expect(
      referenceListOrder().map(
        (id) => REFERENCE_PARTS.find((part) => part.id === id)!.number
      )
    ).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8]);
  });

  it('indents by how far a position sits below the machine', () => {
    expect(referenceDepth('machine')).toBe(0);
    expect(referenceDepth('motor')).toBe(1);
    expect(referenceDepth('bearing')).toBe(2);
    expect(referenceDepth('impeller')).toBe(2);
    expect(referenceDepth('base')).toBe(1);
  });
});
