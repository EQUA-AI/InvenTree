import { describe, expect, it, vi } from 'vitest';

vi.mock('@lingui/core/macro', () => ({
  t: (strings: TemplateStringsArray) => strings.join('')
}));
import { retainedValue } from './occurrenceLabels';

import {
  occurrenceListParams,
  occurrenceListState,
  occurrenceRequest,
  shiftParts
} from './occurrenceModel';

describe('occurrence list state', () => {
  it('defaults to every occurrence, first page, nothing open', () => {
    expect(occurrenceListState(new URLSearchParams())).toEqual({
      status: 'all',
      page: 1,
      open: null
    });
  });

  it('reads a linked occurrence and keeps other parameters', () => {
    const state = occurrenceListState(
      new URLSearchParams('from=machines&fault_status=resolved&occurrence=901')
    );
    expect(state).toEqual({ status: 'resolved', page: 1, open: 901 });
    expect(
      occurrenceListParams(
        new URLSearchParams('from=machines'),
        state
      ).toString()
    ).toBe('from=machines&fault_status=resolved&occurrence=901');
  });

  it('ignores values it does not support', () => {
    expect(
      occurrenceListState(
        new URLSearchParams('fault_status=open&fault_page=0&occurrence=abc')
      )
    ).toEqual({ status: 'all', page: 1, open: null });
  });

  it('asks the server for one bounded page', () => {
    expect(
      occurrenceRequest({ status: 'active', page: 3, open: null })
    ).toEqual({ status: 'active', limit: 25, offset: 50 });
  });
});

describe('shiftParts', () => {
  it('splits a display shift, and is null when nothing moved', () => {
    expect(shiftParts(0)).toBeNull();
    expect(shiftParts(442 * 86400 + 3 * 3600)).toEqual({
      days: 442,
      hours: 3,
      minutes: 0
    });
    expect(shiftParts(-5400)).toEqual({ days: 0, hours: 1, minutes: 30 });
  });
});

describe('retained evidence precision', () => {
  it.each([
    [0.0004, '0.0004 g'],
    [12.34567, '12.34567 g'],
    [0, '0 g']
  ])('keeps every digit of %s', (value, expected) => {
    expect(retainedValue(value, 'g')).toBe(expected);
  });
});
