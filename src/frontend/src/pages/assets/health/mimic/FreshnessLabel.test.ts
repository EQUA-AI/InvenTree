import { describe, expect, it, vi } from 'vitest';
import { ageLabel, freshnessText } from './FreshnessLabel';
import { displayClock } from './observedTime';
import type { MimicPoint } from './types';
describe('response-time freshness without a client threshold', () => {
  it.each([
    [2, '2 s'],
    [840, '14 min'],
    [10800, '3 h'],
    [432000, '5 d'],
    [null, 'age unavailable']
  ] as const)('formats %s', (seconds, label) =>
    expect(ageLabel(seconds)).toBe(label)
  );
  it('labels usable, stale and absent observation times', () => {
    const point = {
      observed_at: '2026-10-20T08:00:00Z',
      age_seconds: 2,
      reason: null
    } as MimicPoint;
    const clock = displayClock('UTC');
    expect(freshnessText(point, clock)).toBe('2 s ago · 08:00:00');
    expect(freshnessText({ ...point, reason: 'stale' }, clock)).toBe(
      'Stale · last observed 08:00:00 (2 s ago)'
    );
    expect(freshnessText({ ...point, observed_at: null }, clock)).toBe(
      'Observation time unavailable'
    );
    expect(freshnessText({ ...point, age_seconds: null }, clock)).toBe(
      'age unavailable · 08:00:00'
    );
    expect(
      freshnessText({ ...point, age_seconds: null, reason: 'stale' }, clock)
    ).toBe('Stale · last observed 08:00:00 (age unavailable)');
  });
});

vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...values: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (values[index] ?? ''), '')
}));
