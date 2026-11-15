import { describe, expect, it, vi } from 'vitest';

vi.mock('@lingui/core/macro', () => ({
  t: (strings: TemplateStringsArray, ...values: unknown[]) =>
    strings.reduce(
      (text, part, index) => text + part + (values[index] ?? ''),
      ''
    )
}));

import {
  composeSourceAnswerMarkdown,
  normalizeSourceObservation,
  sourceObservationLines
} from './sourceObservation';

const observationFixture = {
  schema_version: 'aimms.source-observation.v1',
  kind: 'recorded_history',
  population_count: null,
  complete_population: false,
  revision_complete: false,
  returned_count: 1,
  captured_at: '2026-11-13T12:00:00Z',
  recorded_only: true,
  window: [1000, 5000],
  window_complete: false,
  gaps: [{ window: [3000, 5000], reason: 'source_unavailable' }],
  source_evidence_kind: 'synthetic_fixture',
  citations: [
    {
      ordinal: 2,
      source_type: 'recorded_telemetry',
      source_id: 'sample-1',
      source_title: 'Recorded sample',
      source_revision: 'generation-1',
      source_class: 'telemetry',
      controlled: false,
      as_of: '2026-11-13T11:59:00Z',
      available: true,
      locator: { page: null, section: null, field: 'value' },
      applicability: null,
      evidence_set_id: null,
      calculation: null
    }
  ]
};

describe('source observation envelope', () => {
  it('retains unknowns, actual ordinals, scope and gaps on every envelope', () => {
    const live = normalizeSourceObservation(observationFixture);
    const reloaded = normalizeSourceObservation(
      JSON.parse(JSON.stringify(observationFixture))
    );
    expect(live).toEqual(reloaded);
    expect(live).not.toBeNull();
    if (!live || !reloaded) throw new Error('Expected source fixture');
    expect(composeSourceAnswerMarkdown('12.5 [2]', live)).toEqual(
      composeSourceAnswerMarkdown('12.5 [2]', reloaded)
    );
    const copy = composeSourceAnswerMarkdown('12.5 [2]', live);
    for (const line of sourceObservationLines(live))
      expect(copy).toContain(line);
    expect(copy).toContain('Full population unknown');
    expect(copy).toContain('Synthetic fixture');
    expect(copy).toContain('source_unavailable');
    expect(copy).toContain('[2] Recorded sample');
    expect(copy).not.toContain('0 of 0');
    expect(copy).not.toContain('uncontrolled attachment');
  });
  it('rejects wrong scalar types, false completeness, invalid windows and contradictory gaps', () => {
    for (const invalid of [
      { population_count: 0 },
      { population_count: undefined },
      { complete_population: 0 },
      { complete_population: true },
      { revision_complete: true },
      { returned_count: '1' },
      { returned_count: -1 },
      { returned_count: 1.5 },
      { recorded_only: false },
      { kind: 'live' },
      { window: [5000, 1000] },
      { window: [1, 9e15] },
      { window: [true, 5000] },
      { window_complete: true },
      { captured_at: '2026-11-13T12:00:00' },
      { gaps: [{ window: [4000, 6000], reason: 'outside' }] },
      { citations: [{ ...observationFixture.citations[0], ordinal: '2' }] },
      {
        citations: [
          observationFixture.citations[0],
          observationFixture.citations[0]
        ]
      }
    ])
      expect(
        normalizeSourceObservation({ ...observationFixture, ...invalid })
      ).toBeNull();
  });
  it('strips private fields and projects unavailable without retained source labels', () => {
    const normalized = normalizeSourceObservation({
      ...observationFixture,
      _aimms_source_ref: 'private'
    });
    expect(normalized).not.toHaveProperty('_aimms_source_ref');
    const denied = normalizeSourceObservation({
      ...observationFixture,
      state: 'unavailable'
    });
    expect(denied).toEqual({
      schema_version: 'aimms.source-observation.v1',
      state: 'unavailable'
    });
    if (!denied) throw new Error('Expected unavailable');
    expect(composeSourceAnswerMarkdown('Unavailable', denied)).not.toContain(
      'sample-1'
    );
  });
  it('keeps a selected record separate from a history window', () => {
    const record = {
      ...observationFixture,
      kind: 'record',
      recorded_only: false,
      window: null,
      window_complete: null,
      gaps: [],
      source_evidence_kind: null
    };
    expect(normalizeSourceObservation(record)).not.toBeNull();
    expect(
      normalizeSourceObservation({ ...record, returned_count: 0 })
    ).toBeNull();
  });
});
