import { expect, it } from 'vitest';
import { presentationTicks } from './axisPresentation';
it('floors known nonnegative measurements without hiding supplied negatives', () => {
  expect(presentationTicks([-1, 0, 1], 0, 'MW')).toEqual([0, 1]);
  expect(presentationTicks([-1, 0, 1], -0.2, 'MW')).toEqual([-1, 0, 1]);
  expect(presentationTicks([-1, 0, 1], 0, 'degC')).toEqual([-1, 0, 1]);
});
it('uses distinct integer ticks for counts including a flat zero', () => {
  expect(presentationTicks([-1, -0.5, 0, 0.5, 1], 0, '', true)).toEqual([0, 1]);
  expect(presentationTicks([0, 0.1], 0, '', true)).toEqual([0, 1]);
});
