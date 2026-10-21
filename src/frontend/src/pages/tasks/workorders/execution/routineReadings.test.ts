import type { RoutineWork } from '@lib/types/RoutineWork';
import { describe, expect, it } from 'vitest';
import { groupRoutineReadings } from './routineReadings';

type Reading = RoutineWork['readings'][number];
const reading = (
  id: number,
  step: number | null,
  label = 'Pressure'
): Reading => ({
  id,
  step_execution: step,
  label,
  phase: id === 1 ? 'before' : 'after',
  raw_text: '0 bar',
  value: '0',
  unit: 'bar',
  required: true,
  verification_state: 'pending',
  warnings: [],
  expected_min: null,
  expected_max: null,
  disposition_reason: ''
});
describe('routine reading associations', () => {
  it('keeps recorded phases together only for the same step and label', () => {
    const rows = [
      reading(1, 9),
      reading(2, 9),
      reading(3, 10),
      reading(4, 9, 'Temperature')
    ];
    const groups = groupRoutineReadings(rows);
    expect(groups.map((group) => group.readings.map((row) => row.id))).toEqual([
      [1, 2],
      [3],
      [4]
    ]);
    expect(groups[0].readings.map((row) => row.phase)).toEqual([
      'before',
      'after'
    ]);
    expect(groups[0].readings[0]).toBe(rows[0]);
  });
  it('never pairs unlinked readings by label or replaces retained attempts', () => {
    const groups = groupRoutineReadings([
      reading(1, null),
      reading(2, null),
      reading(3, 9),
      reading(4, 9),
      reading(5, 9)
    ]);
    expect(groups.map((group) => group.readings.map((row) => row.id))).toEqual([
      [1],
      [2],
      [3, 4, 5]
    ]);
  });
  it('does not create readings for an empty result', () =>
    expect(groupRoutineReadings([])).toEqual([]));
});
