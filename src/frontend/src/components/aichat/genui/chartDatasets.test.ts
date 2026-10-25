import { expect, it } from 'vitest';
import { normalizePresentationData } from './datasets';
import { normalizePresentation } from './presentation';
import { validateProgram } from './program';

const provided = () => ({
  kind: 'chart_dataset',
  source_key: 'provided:series',
  origin: 'user_supplied',
  chart_type: 'line',
  title: 'Synthetic test data',
  unit: 'value',
  x_label: 'Month',
  y_label: 'Test count',
  source: 'user_supplied',
  source_revision: 'test-turn',
  as_of: '2026-10-25T12:00:00Z',
  scope_label: 'Values supplied in this message',
  definition: 'Synthetic values, not operational records.',
  population_count: null,
  complete_population: null,
  points: [
    { label: 'Jan', value: 4 },
    { label: 'Feb', value: 0 },
    { label: 'Mar', value: null },
    { label: 'Apr', value: 9 }
  ]
});
it('preserves zeros, gaps and supplied provenance with detached historical values', () => {
  const raw = provided();
  const normalized = normalizePresentationData([raw]);
  expect(normalized).toEqual([raw]);
  raw.points[0].value = 100;
  expect(
    normalized[0].kind === 'chart_dataset' && normalized[0].points[0].value
  ).toBe(4);
});
it('rejects forged coverage, duplicates, invalid numbers and invalid pie totals as a whole', () => {
  const raw = provided();
  for (const invalid of [
    { ...raw, origin: 'recorded' },
    { ...raw, population_count: 13 },
    { ...raw, source: 'work_orders' },
    { ...raw, chart_type: 'radar' },
    { ...raw, points: [{ label: 'A', value: Number.NaN }] },
    {
      ...raw,
      points: [
        { label: 'A', value: 1 },
        { label: 'A', value: 2 }
      ]
    },
    { ...raw, chart_type: 'pie', points: [{ label: 'A', value: 0 }] },
    {
      ...raw,
      chart_type: 'pie',
      points: [
        { label: 'A', value: -1 },
        { label: 'B', value: 5 }
      ]
    },
    { ...raw, points: [{ label: 'A', value: 1e13 }] }
  ])
    expect(normalizePresentationData([raw, invalid])).toEqual([]);
});
it('requires complete recorded counts to reconcile with the stated population', () => {
  const raw = {
    ...provided(),
    origin: 'recorded',
    source: 'work_orders',
    complete_population: true,
    population_count: 5,
    points: [
      { label: 'Open', value: 3 },
      { label: 'Completed', value: 2 }
    ]
  };
  expect(normalizePresentationData([raw])).toEqual([raw]);
  expect(normalizePresentationData([{ ...raw, population_count: 6 }])).toEqual(
    []
  );
  expect(
    normalizePresentationData([{ ...raw, complete_population: false }])
  ).toEqual([]);
});
it('supports chart references only in the version-two library and preserves version one', () => {
  const program = 'root = AnswerLayout([AnswerSummary(),ChartSnapshot("r0")])';
  const envelope = {
    schema_version: 1,
    format: 'openui-lang',
    library: 'aimms-chat@2',
    program,
    references: [
      { ref_id: 'r0', kind: 'chart_dataset', source_key: 'provided:series' }
    ]
  };
  expect(normalizePresentation(envelope)).toEqual(envelope);
  expect(validateProgram(program, 'aimms-chat@2')).toBe(true);
  expect(validateProgram(program, 'aimms-chat@1')).toBe(false);
  expect(
    normalizePresentation({ ...envelope, library: 'aimms-chat@1' })
  ).toBeNull();
  expect(
    validateProgram(
      'root = AnswerLayout([AnswerSummary(),AssetSummary("r0")])',
      'aimms-chat@1'
    )
  ).toBe(true);
});
