import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { UnavailableReading } from './MimicValue';
import {
  DisplayClockContext,
  displayClock,
  formatInstant
} from './observedTime';
import type { MimicPoint } from './types';

vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (args[index] ?? ''), '')
}));

const point = (value: MimicPoint['value'], reason: string): MimicPoint => ({
  pointer: '/power',
  label: 'Input power',
  group: 'Motor',
  value,
  reason,
  unit: 'degC',
  quality: reason === 'stale' ? 'good' : 'bad',
  observed_at: '2026-09-13T12:00:00Z',
  age_seconds: 500,
  unchanged_for_seconds: null,
  condition: 'unknown',
  thresholds_configured: false
});
const clock = displayClock('Asia/Riyadh');
const render = (reading: MimicPoint) =>
  renderToStaticMarkup(
    createElement(
      MantineProvider,
      {},
      createElement(
        DisplayClockContext,
        { value: clock },
        createElement(UnavailableReading, {
          point: reading,
          label: 'Input power'
        })
      )
    )
  );

it('preserves the actual stale observation and plant clock in the accessible missing value', () => {
  const html = render(point(null, 'stale'));
  expect(html).toContain(
    `aria-label="Input power: Stale · ${formatInstant('2026-09-13T12:00:00Z', clock)} · Asia/Riyadh`
  );
  expect(html).toContain('15:00:00');
  expect(html).toContain('last observed 15:00:00');
  expect(html).toMatch(/<button[^>]*>—<\/button>/);
});

it('retains a finite known value while exposing its bad quality', () => {
  const html = render(point(95, 'bad_quality'));
  expect(html).toContain('95 °C');
  expect(html).toContain('Bad quality');
});

it.each([Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])(
  'does not present %s as a known measurement',
  (value) => {
    const html = render(point(value, 'not_finite'));
    expect(html).toContain('Read failed');
    expect(html).not.toContain('Bad quality');
    expect(html).not.toContain('Infinity');
    expect(html).not.toContain('NaN');
  }
);
