import { expect, it, vi } from 'vitest';
import { metricLabel, outsideOccurrenceSpan } from './occurrenceLabels';
it('labels only declared detector directions and units', () => {
  const metrics = { warn_max: 110, unit: 'degC', peak: 1.8 };
  expect(metricLabel('warn_max', 110, metrics)).toBe(
    'High warning limit 110 °C'
  );
  expect(metricLabel('warn_min', null, metrics)).toBeNull();
  expect(metricLabel('peak', 1.8, metrics)).toBe('peak: 1.8');
  expect(metricLabel('threshold', 2, metrics)).toBe('threshold: 2');
  expect(
    metricLabel('vote_confirmed', 2, { vote_minimum: 3, vote_group: 'thermal' })
  ).toBe('Confirmed by 2 of 3 detectors in thermal');
});
it('compares retained windows exactly with the observation span', () => {
  const span = {
    first_observed_at: '2026-10-12T02:00:00Z',
    last_observed_at: '2026-10-12T03:00:00Z'
  };
  expect(
    outsideOccurrenceSpan(
      {
        window_start: span.first_observed_at,
        window_end: span.last_observed_at
      },
      span
    )
  ).toBe(false);
  expect(
    outsideOccurrenceSpan(
      {
        window_start: '2025-07-27T02:14:05Z',
        window_end: '2025-07-27T02:14:05Z'
      },
      span
    )
  ).toBe(true);
  expect(
    outsideOccurrenceSpan(
      {
        window_start: span.last_observed_at,
        window_end: span.last_observed_at
      },
      span
    )
  ).toBe(false);
  expect(
    outsideOccurrenceSpan({ window_start: null, window_end: null }, span)
  ).toBe(false);
});

vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...values: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (values[index] ?? ''), '')
}));
