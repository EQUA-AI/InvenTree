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

  it('lists each assembly before what it holds, every position once', () => {
    expect(referenceListOrder()).toEqual([
      'machine',
      'motor',
      'bearing',
      'coupling',
      'pump',
      'seal',
      'impeller',
      'discharge',
      'base'
    ]);
  });

  it('indents by how far a position sits below the machine', () => {
    expect(referenceDepth('machine')).toBe(0);
    expect(referenceDepth('motor')).toBe(1);
    expect(referenceDepth('bearing')).toBe(2);
    expect(referenceDepth('impeller')).toBe(2);
    expect(referenceDepth('base')).toBe(1);
  });
});
