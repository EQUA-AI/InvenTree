import { describe, expect, it } from 'vitest';

import { machineWorkSummary } from './machineWork';

describe('machineWorkSummary', () => {
  it('reports open Work with its highest priority', () => {
    expect(machineWorkSummary({ count: 3, highest_priority: 'high' })).toEqual({
      kind: 'open',
      count: 3,
      priority: 'high'
    });
    expect(machineWorkSummary({ count: 1, highest_priority: null })).toEqual({
      kind: 'open',
      count: 1,
      priority: null
    });
  });

  it('reports no open Work only for an explicit zero', () => {
    expect(machineWorkSummary({ count: 0, highest_priority: null })).toEqual({
      kind: 'none'
    });
  });

  it.each([
    ['an unresolved scope', null],
    ['an older server', undefined],
    ['a string count', { count: '3', highest_priority: 'high' }],
    ['a negative count', { count: -1, highest_priority: null }],
    ['a fractional count', { count: 1.5, highest_priority: 'low' }],
    ['an unknown priority', { count: 2, highest_priority: 'urgent' }],
    ['a priority on zero Work', { count: 0, highest_priority: 'high' }],
    ['an array', [{ count: 1 }]]
  ])('treats %s as not available, never as zero', (_label, value) => {
    expect(machineWorkSummary(value)).toEqual({ kind: 'unavailable' });
  });
});
