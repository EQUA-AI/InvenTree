import { expect, it } from 'vitest';
import { deviationFacts } from './packagePresentation';

it('keeps true, false, zero and unknown distinct in readable field paths', () => {
  expect(
    deviationFacts({
      access: true,
      isolation: false,
      readings: { recorded_value: 0 },
      permit: null
    })
  ).toEqual([
    { field: 'access', value: true },
    { field: 'isolation', value: false },
    { field: 'readings · recorded value', value: 0 },
    { field: 'permit', value: null }
  ]);
});
it('retains nested lists and original text without inventing empty values', () => {
  expect(deviationFacts({ notes: ['Retained text', 'Second note'] })).toEqual([
    { field: 'notes · 1', value: 'Retained text' },
    { field: 'notes · 2', value: 'Second note' }
  ]);
  expect(deviationFacts({})).toEqual([]);
  expect(deviationFacts(undefined)).toEqual([]);
});
