/** Synthetic source observation; never runtime source evidence. */
export const payload = {
  schema_version: 'aimms.source-observation.v1',
  kind: 'recorded_history',
  population_count: null,
  complete_population: false,
  revision_complete: false,
  returned_count: 1,
  captured_at: '2026-11-13T12:00:00Z',
  recorded_only: true,
  window: [1794567600000, 1794571200000],
  window_complete: false,
  gaps: [
    { window: [1794569400000, 1794571200000], reason: 'source_unavailable' }
  ],
  source_evidence_kind: 'synthetic_fixture',
  citations: [
    {
      ordinal: 2,
      source_type: 'recorded_telemetry',
      source_id: 'synthetic-sample-1',
      source_title: 'Recorded bearing temperature',
      source_revision: 'fixture-generation-1',
      source_class: 'telemetry',
      controlled: false,
      as_of: '2026-11-13T11:59:00Z',
      available: true,
      locator: {
        page: null,
        section: null,
        field: 'value · °C · quality: good'
      },
      applicability: null,
      evidence_set_id: null,
      calculation: null
    }
  ]
};
export const body =
  'The recorded bearing temperature was **12.5 °C** [2]. This selected interval has a reported gap; the result describes recorded history.';
