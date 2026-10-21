import { expect, it, vi } from 'vitest';

import { dischargeFlowReading } from './format';
import type { MimicData } from './types';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...values: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (values[index] ?? ''), '')
}));
it.each([
  [0, 'good', null, '0 m³/s'],
  [0.4, 'good', null, '0.4 m³/s'],
  [null, 'good', null, null],
  [2, 'bad', null, null],
  [2, 'good', 'stale', null],
  [Number.NaN, 'good', null, null],
  [Number.POSITIVE_INFINITY, 'good', null, null],
  ['2', 'good', null, null]
])('discharge flow %s %s %s', (value, quality, reason, expected) => {
  const data = {
    layout: {
      elements: [{ id: 'pump-flow', view: 'unit', pointer: '/pd/{pump}/dv' }]
    },
    points: { '/pd/P1/dv': { value, quality, reason, unit: 'm³/s' } }
  } as unknown as MimicData;
  expect(dischargeFlowReading(data, 'P1')).toBe(expected);
});
it('does not invent an absent discharge mapping', () =>
  expect(
    dischargeFlowReading(
      { layout: { elements: [] }, points: {} } as unknown as MimicData,
      'P1'
    )
  ).toBeNull());
