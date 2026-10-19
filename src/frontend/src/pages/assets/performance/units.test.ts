import { describe, expect, it } from 'vitest';

import {
  groupByScale,
  largestGroup,
  sameScale,
  scaleKey,
  splitAxes,
  unitText
} from './units';

describe('scaleKey / sameScale', () => {
  it('lets power units share a scale only at the same prefix', () => {
    expect(sameScale('MW', 'MVar')).toBe(true);
    expect(sameScale('MW', 'Mvar')).toBe(true);
    expect(sameScale('kW', 'kvar')).toBe(true);
    expect(sameScale('kW', 'kVA')).toBe(true);
    expect(sameScale('MW', 'kW')).toBe(false);
    expect(sameScale('MW', 'kvar')).toBe(false);
  });

  it('compares every other unit as written, and never joins an empty unit to a stated one', () => {
    expect(sameScale('degC', 'degC')).toBe(true);
    expect(sameScale('degC', '°C')).toBe(false);
    expect(sameScale('kV', 'V')).toBe(false);
    expect(sameScale('', 'percent')).toBe(false);
    expect(sameScale('', '')).toBe(true);
    expect(sameScale(' m ', 'm')).toBe(true);
    expect(sameScale(null, '')).toBe(true);
  });

  it('does not read a milli prefix as mega', () => {
    expect(scaleKey('mW')).toBe('unit:mW');
    expect(sameScale('mW', 'MW')).toBe(false);
  });
});

describe('groupByScale', () => {
  it('groups in first-seen order and keeps each written unit', () => {
    const groups = groupByScale(
      [
        { id: 1, unit: 'MW' },
        { id: 2, unit: 'kW' },
        { id: 3, unit: 'MVar' },
        { id: 4, unit: 'kW' }
      ],
      (item) => item.unit
    );
    expect(groups.map((g) => g.items.map((i) => i.id))).toEqual([
      [1, 3],
      [2, 4]
    ]);
    expect(groups.map(unitText)).toEqual(['MW / MVar', 'kW']);
    expect(largestGroup(groups)?.units).toEqual(['MW', 'MVar']);
  });

  it('breaks a tie towards the earliest group', () => {
    const groups = groupByScale(['V', 'kV'], (unit) => unit);
    expect(largestGroup(groups)?.units).toEqual(['V']);
    expect(largestGroup([])).toBeUndefined();
  });
});

describe('splitAxes', () => {
  it('keeps a chart whole when each axis carries one scale', () => {
    const split = splitAxes([
      { key: 'speed', unit: 'rpm' },
      { key: 'status', unit: '', yAxisId: 'right' as const },
      { key: 'motor', unit: '', yAxisId: 'right' as const }
    ]);
    expect(split.apart).toEqual([]);
    expect(split.main.map((l) => l.key)).toEqual(['speed', 'status', 'motor']);
    expect(split.leftWhole && split.rightWhole).toBe(true);
    expect(split.leftUnits).toBe('rpm');
  });

  it('sets a second scale on an axis apart, on either side', () => {
    const split = splitAxes([
      { key: 'avg', unit: 'A' },
      { key: 'phase', unit: 'kA' },
      { key: 'line', unit: 'kV', yAxisId: 'right' as const },
      { key: 'ry', unit: 'V', yAxisId: 'right' as const },
      { key: 'yb', unit: 'V', yAxisId: 'right' as const }
    ]);
    expect(split.main.map((l) => l.key)).toEqual(['avg', 'line']);
    expect(split.leftWhole).toBe(false);
    expect(split.rightWhole).toBe(false);
    expect(
      split.apart.map((a) => [a.axis, a.group.items.map((l) => l.key)])
    ).toEqual([
      ['left', ['phase']],
      ['right', ['ry', 'yb']]
    ]);
  });

  it('keeps active and reactive power together at one magnitude', () => {
    const split = splitAxes([
      { key: 'p', unit: 'MW' },
      { key: 'q', unit: 'MVar' },
      { key: 'pf', unit: '', yAxisId: 'right' as const }
    ]);
    expect(split.apart).toEqual([]);
    expect(split.leftUnits).toBe('MW / MVar');
  });
});
