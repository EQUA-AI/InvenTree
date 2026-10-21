import { describe, expect, it } from 'vitest';
import { thresholdBands } from './thresholdBands';
describe('configured threshold bands', () => {
  it('separates upper warning and critical bands', () => {
    expect(
      thresholdBands(
        [
          { y: 80, direction: 'high', severity: 'warning' },
          { y: 100, direction: 'high', severity: 'critical' }
        ],
        60,
        120
      )
    ).toEqual([
      { from: 80, to: 100, severity: 'warning' },
      { from: 100, to: 120, severity: 'critical' }
    ]);
  });
  it('separates lower critical and warning bands', () => {
    expect(
      thresholdBands(
        [
          { y: 20, direction: 'low', severity: 'warning' },
          { y: 10, direction: 'low', severity: 'critical' }
        ],
        0,
        30
      )
    ).toEqual([
      { from: 0, to: 10, severity: 'critical' },
      { from: 10, to: 20, severity: 'warning' }
    ]);
  });
  it('never invents a region for a directionless reference or absent boundary', () => {
    expect(thresholdBands([{ y: 5 }], 0, 10)).toEqual([]);
    expect(thresholdBands([], 0, 10)).toEqual([]);
  });
  it('clips to the actual scale and rejects invalid domains', () => {
    expect(
      thresholdBands(
        [{ y: 0, direction: 'high', severity: 'critical' }],
        10,
        20
      )
    ).toEqual([{ from: 10, to: 20, severity: 'critical' }]);
    expect(thresholdBands([], Number.NaN, 20)).toEqual([]);
  });
});
