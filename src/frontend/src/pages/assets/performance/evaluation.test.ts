import { describe, expect, it } from 'vitest';

import { evaluatePerformance, reactiveSharesPowerAxis } from './evaluation';

const limits = {
  normal_min: null,
  normal_max: null,
  warn_min: null,
  warn_max: 125,
  critical_min: null,
  critical_max: 145
};
const none = { ...limits, warn_max: null, critical_max: null };
const reading = (
  state: string,
  extra: { stale?: boolean; quality?: string; limited?: boolean } = {}
) => ({
  state: state as 'normal',
  stale: extra.stale ?? false,
  quality: (extra.quality ?? 'good') as 'good',
  limits: extra.limited === false ? none : limits
});

describe('Anything unusual verdict', () => {
  it('never calls a machine calm when no signal has a limit', () => {
    const result = evaluatePerformance([
      reading('unknown', { limited: false }),
      reading('unknown', { limited: false })
    ]);
    expect(result).toEqual({
      verdict: { kind: 'no-limits' },
      evaluated: 0,
      configured: 0,
      total: 2
    });
    expect(evaluatePerformance([]).verdict.kind).toBe('no-limits');
  });

  it('says evaluation is unavailable when limited readings cannot be judged', () => {
    const result = evaluatePerformance([
      reading('unknown', { stale: true }),
      reading('unknown', { quality: 'bad' }),
      reading('unknown', { limited: false })
    ]);
    expect(result.verdict.kind).toBe('unavailable');
    expect([result.evaluated, result.configured]).toEqual([0, 2]);
  });

  it('is calm only when every judged reading is in limits and none is unusable', () => {
    const result = evaluatePerformance([
      reading('normal'),
      reading('normal'),
      reading('unknown', { limited: false })
    ]);
    expect(result).toEqual({
      verdict: { kind: 'calm' },
      evaluated: 2,
      configured: 2,
      total: 3
    });
  });

  it('does not claim calm beside a stale or poor reading', () => {
    expect(
      evaluatePerformance([
        reading('normal'),
        reading('unknown', { stale: true })
      ]).verdict.kind
    ).toBe('limited');
    expect(
      evaluatePerformance([
        reading('normal'),
        reading('unknown', { quality: 'uncertain', limited: false })
      ]).verdict.kind
    ).toBe('limited');
  });

  it('does not claim calm while a limited signal went unjudged', () => {
    // Fresh and good, but nothing to compare: the server leaves it unknown.
    expect(
      evaluatePerformance([reading('normal'), reading('unknown')]).verdict.kind
    ).toBe('limited');
  });

  it('reports readings outside their limits whatever else is missing', () => {
    expect(
      evaluatePerformance([
        reading('warning'),
        reading('unknown', { stale: true })
      ]).verdict.kind
    ).toBe('outside');
    expect(evaluatePerformance([reading('critical')]).verdict.kind).toBe(
      'outside'
    );
  });
});

describe('reactive power on the active-power axis', () => {
  it.each([
    [['MW'], ['MVar']],
    [['MW'], ['Mvar']],
    [['kW'], ['kvar', 'kVAr']],
    [['W'], ['var']]
  ])('shares the axis for %j with %j', (power, reactive) => {
    expect(reactiveSharesPowerAxis(power, reactive)).toBe(true);
  });

  it.each([
    [['MW'], ['kvar']],
    [['kW'], ['']],
    [['MW'], [null]],
    [['mW'], ['mvar']],
    [['MW', 'kW'], ['Mvar']],
    [['percent'], ['Mvar']],
    [[], ['Mvar']],
    [['MW'], []]
  ])('keeps them apart for %j with %j', (power, reactive) => {
    expect(reactiveSharesPowerAxis(power, reactive)).toBe(false);
  });
});
