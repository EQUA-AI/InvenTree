import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it, vi } from 'vitest';
import { unconfiguredCount } from './MimicAlarmCoverage';
import { compactReadings } from './MimicParts';
import { ReadingSections, summarize } from './MimicReadings';
import { failedReading } from './MimicValue';
import { valueText } from './format';
import type { MimicPoint } from './types';

vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...values: unknown[]) =>
    parts.reduce((text, part, index) => text + part + (values[index] ?? ''), '')
}));

const point = (
  value: number | null,
  extra: Partial<MimicPoint> = {}
): MimicPoint => ({
  pointer: `/sensor/${value}`,
  label: 'Discharge line temperature',
  group: 'Discharge line',
  value,
  unit: 'degC',
  quality: 'good',
  observed_at: null,
  age_seconds: null,
  unchanged_for_seconds: null,
  reason: value === null ? 'not_published' : null,
  condition: 'unknown',
  thresholds_configured: false,
  ...extra
});

it('collapses equal readings and formats the engineering unit', () => {
  expect(summarize([point(0), point(0), point(0)]).lines).toEqual([
    '3 readings: 0 °C'
  ]);
});

it('uses the reviewed flow label instead of an internal key in summaries', () => {
  expect(
    summarize([point(0, { label: 'dv', pointer: '/pd/P7/dv', unit: 'cusec' })])
      .lines
  ).toEqual(['Discharge flow: 0 cusec']);
});

it('keeps callout values compact while preserving different source units', () => {
  expect(
    compactReadings(
      [point(39.3), point(39.6), point(0, { unit: 'cusec' })],
      valueText
    )
  ).toBe('39.3 – 39.6 °C · 0 cusec');
  expect(compactReadings([point(0), point(0), point(null)], valueText)).toBe(
    '0 °C'
  );
});

it('does not call unconfigured readings normal', () => {
  expect(summarize([point(39.3, { condition: 'normal' })]).verdict).toBeNull();
});

it('keeps the actual configured critical verdict available without a mini-bar scale', () => {
  expect(
    summarize([
      point(95, { thresholds_configured: true, condition: 'critical' })
    ]).verdict
  ).toBe('critical');
});

it('does not substitute an unknown limit scope with zero readings without limits', () => {
  expect(unconfiguredCount([])).toBeNull();
  const missingFlag = point(1);
  delete (missingFlag as Partial<MimicPoint>).thresholds_configured;
  expect(unconfiguredCount([missingFlag])).toBeNull();
  expect(
    unconfiguredCount([point(1), point(2, { thresholds_configured: true })])
  ).toBe(1);
});

it('distinguishes failed quality from an absent point or an unpublished reading', () => {
  expect(
    failedReading(point(95, { reason: 'bad_quality', quality: 'bad' }))
  ).toBe(true);
  expect(failedReading(point(null, { reason: 'type_mismatch' }))).toBe(true);
  expect(failedReading(point(null, { reason: 'not_published' }))).toBe(false);
  expect(failedReading(undefined)).toBe(false);
});

it('does not promote an independently bad quality flag into a normal verdict or compact reading', () => {
  const bad = point(95, {
    quality: 'bad',
    reason: null,
    condition: 'normal',
    thresholds_configured: true
  });
  expect(summarize([bad])).toMatchObject({
    verdict: 'unknown',
    lines: [],
    unavailable: 1
  });
  expect(compactReadings([bad], valueText)).toBe('');
});

it.each([
  ['normal', 'normal'],
  ['unknown', 'neutral']
] as const)(
  'uses the canonical %s status while preserving a native part number',
  (condition, tone) => {
    const html = renderToStaticMarkup(
      createElement(
        MantineProvider,
        {},
        createElement(ReadingSections, {
          sections: [
            {
              key: 'motor',
              title: 'Motor',
              number: 3,
              points: [point(10, { thresholds_configured: true, condition })]
            }
          ]
        })
      )
    );
    expect(html).toMatch(
      new RegExp(
        `<span(?=[^>]*data-tone="${tone}")(?=[^>]*data-variant="outline")[^>]*>`
      )
    );
    expect(html).toMatch(
      /<div(?=[^>]*data-number="3")(?=[^>]*data-variant="filled")[^>]*>/
    );
  }
);
