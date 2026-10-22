import { expect, it, vi } from 'vitest';

import {
  dischargeFlowReading,
  numberText,
  readingLabel,
  valueText
} from './format';
import type { MimicData, MimicPoint } from './types';
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

it.each([
  ['degC', '39.3 °C'],
  ['degF', '39.3 °F'],
  ['percent', '39.3 %'],
  ['cusec', '39.3 cusec'],
  ['m3/s', '39.3 m³/s'],
  ['m^3/s', '39.3 m³/s']
])('formats %s without changing its source magnitude', (unit, expected) => {
  expect(valueText({ value: 39.3, reason: null, unit } as MimicPoint)).toBe(
    expected
  );
});

it.each([-0, -0.00001, -0.0001])(
  'does not display rounded negative zero for %s',
  (value) => {
    expect(numberText(value)).toBe('0');
  }
);

it('preserves a genuine negative pressure and names only the reviewed discharge key', () => {
  expect(
    valueText({ value: -0.003, reason: null, unit: 'mH2O' } as MimicPoint)
  ).toBe('-0.003 mH₂O');
  expect(readingLabel({ label: 'dv', pointer: '/pd/P7/dv' })).toBe(
    'Discharge flow'
  );
  expect(readingLabel({ label: 'dv', pointer: '/unmapped/dv' })).toBe('dv');
});
