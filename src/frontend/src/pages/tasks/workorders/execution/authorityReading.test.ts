import { expect, it } from 'vitest';
import { authorityReading } from './authorityReading';
it('distinguishes recorded run/count zero or false from missing telemetry', () => {
  expect(authorityReading({ value: 0 })).toBe('0');
  expect(authorityReading({ value: false })).toBe('false');
  expect(authorityReading({ value: 'R' })).toBe('R');
  for (const value of [
    null,
    undefined,
    {},
    { value: null },
    { value: '' },
    { value: Number.NaN }
  ])
    expect(authorityReading(value)).toBeNull();
});
