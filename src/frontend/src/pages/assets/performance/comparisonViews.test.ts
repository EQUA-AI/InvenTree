import { describe, expect, it } from 'vitest';
import {
  comparisonGroups,
  displayViewKey,
  parseDisplayViews
} from './comparisonViews';
import type { ResolvedParameter } from './resolve';
const parameter = (id: number, role: string, unit: string) =>
  ({
    id: String(id),
    definition: { role },
    signal: { binding_id: id, unit }
  }) as ResolvedParameter;
describe('comparison compatibility', () => {
  it('keeps same units with different engineering meaning separate', () => {
    expect(
      comparisonGroups([
        parameter(1, 'speed', 'rpm'),
        parameter(2, 'frequency', 'rpm')
      ])
    ).toHaveLength(2);
  });
  it('groups matching roles and scales, separating incompatible magnitudes', () => {
    const groups = comparisonGroups([
      parameter(1, 'winding', 'degC'),
      parameter(2, 'winding', 'degC'),
      parameter(3, 'winding', 'degF')
    ]);
    expect(groups.map((group) => group.items.length)).toEqual([2, 1]);
  });
  it('never assumes uncatalogued tags with the same unit are the same quantity', () => {
    expect(
      comparisonGroups([
        parameter(1, 'other', 'bar'),
        parameter(2, 'other', 'bar')
      ])
    ).toHaveLength(2);
  });
});
describe('personal display views', () => {
  it('isolates machine, actor and host identities', () => {
    expect(
      new Set([
        displayViewKey('a', 1, 2),
        displayViewKey('b', 1, 2),
        displayViewKey('a', 2, 2),
        displayViewKey('a', 1, 3)
      ]).size
    ).toBe(4);
  });
  it('validates a saved display view and rejects corrupt or operational data', () => {
    const view = {
      name: 'Winding',
      search: 'temp',
      section: 'winding',
      bindings: [1, 2]
    };
    expect(parseDisplayViews(JSON.stringify([view]))).toEqual([view]);
    expect(parseDisplayViews('broken')).toEqual([]);
    expect(
      parseDisplayViews(
        JSON.stringify([
          { ...view, bindings: [-1] },
          { ...view, section: 'safety' }
        ])
      )
    ).toEqual([]);
  });
});
