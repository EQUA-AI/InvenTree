/** One source observation on legacy SSE, AG-UI, reload and answer export. */
import type {
  CitationManifestEntry,
  SourceObservationPayload,
  SourceObservationUnavailable
} from '@lib/types/AimmsWire.generated';
import { t } from '@lingui/core/macro';
import {
  citationDisplayFromManifest,
  formatCitationLine
} from './citationFormat';

export type SourceObservation =
  | SourceObservationPayload
  | SourceObservationUnavailable;

/** Document control badges describe attachments, not a telemetry sample or Work. */
export function sourceCitationDisplay(entry: CitationManifestEntry) {
  const display = citationDisplayFromManifest(entry);
  if (
    !['native_attachment', 'controlled_document'].includes(
      entry.source_class ?? ''
    )
  ) {
    display.controlled = undefined;
  }
  return display;
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function interval(value: unknown): value is [number, number] {
  return (
    Array.isArray(value) &&
    value.length === 2 &&
    value.every((v) => Number.isSafeInteger(v) && Math.abs(v) <= 8.64e15) &&
    value[0] < value[1]
  );
}

function citation(value: unknown): value is CitationManifestEntry {
  if (
    !record(value) ||
    !Number.isSafeInteger(value.ordinal) ||
    (value.ordinal as number) < 1 ||
    typeof value.source_type !== 'string' ||
    typeof value.as_of !== 'string' ||
    typeof value.available !== 'boolean' ||
    typeof value.controlled !== 'boolean'
  )
    return false;
  for (const key of [
    'source_id',
    'source_title',
    'source_revision',
    'source_class',
    'applicability',
    'evidence_set_id',
    'calculation'
  ]) {
    if (value[key] !== null && typeof value[key] !== 'string') return false;
  }
  const locator = value.locator;
  return (
    locator === null ||
    (record(locator) &&
      (locator.page === null ||
        (Number.isSafeInteger(locator.page) &&
          (locator.page as number) >= 1)) &&
      (locator.section === null || typeof locator.section === 'string') &&
      (locator.field === null || typeof locator.field === 'string'))
  );
}

/** Reject coercion and incoherent scope; no guessed zero or partial manifest. */
export function normalizeSourceObservation(
  raw: unknown
): SourceObservation | null {
  if (!record(raw) || raw.schema_version !== 'aimms.source-observation.v1')
    return null;
  if (raw.state === 'unavailable') {
    return {
      schema_version: 'aimms.source-observation.v1',
      state: 'unavailable'
    };
  }
  if (
    raw.population_count !== null ||
    raw.complete_population !== false ||
    raw.revision_complete !== false ||
    !Number.isSafeInteger(raw.returned_count) ||
    (raw.returned_count as number) < 0 ||
    typeof raw.captured_at !== 'string' ||
    !/(Z|[+-]\d{2}:\d{2})$/.test(raw.captured_at) ||
    !Number.isFinite(Date.parse(raw.captured_at)) ||
    !Array.isArray(raw.gaps) ||
    !Array.isArray(raw.citations) ||
    raw.citations.length > 2048 ||
    !raw.citations.every(citation) ||
    new Set(raw.citations.map((c) => c.ordinal)).size !==
      raw.citations.length ||
    (raw.source_evidence_kind !== null &&
      raw.source_evidence_kind !== 'synthetic_fixture')
  )
    return null;
  if (raw.kind === 'record') {
    if (
      raw.recorded_only !== false ||
      raw.window !== null ||
      raw.window_complete !== null ||
      raw.gaps.length !== 0 ||
      raw.returned_count !== 1 ||
      raw.source_evidence_kind !== null
    )
      return null;
  } else if (raw.kind === 'recorded_history') {
    if (
      raw.recorded_only !== true ||
      !interval(raw.window) ||
      typeof raw.window_complete !== 'boolean'
    )
      return null;
    const window = raw.window;
    if (
      raw.gaps.some(
        (g) =>
          !record(g) ||
          !interval(g.window) ||
          typeof g.reason !== 'string' ||
          !g.reason.trim() ||
          g.window[0] < window[0] ||
          g.window[1] > window[1]
      ) ||
      (raw.window_complete && raw.gaps.length > 0)
    )
      return null;
  } else return null;
  // Detach and allow-list: private coordinates and unknown fields never persist client-side.
  return {
    schema_version: 'aimms.source-observation.v1',
    kind: raw.kind,
    population_count: null,
    complete_population: false,
    revision_complete: false,
    returned_count: raw.returned_count as number,
    captured_at: raw.captured_at,
    recorded_only: raw.recorded_only as boolean,
    window: raw.window as number[] | null,
    window_complete: raw.window_complete as boolean | null,
    gaps: raw.gaps.map((g) => ({ window: [...g.window], reason: g.reason })),
    source_evidence_kind: raw.source_evidence_kind,
    citations: raw.citations.map((c) => ({
      ordinal: c.ordinal,
      source_type: c.source_type,
      source_id: c.source_id,
      source_title: c.source_title,
      source_revision: c.source_revision,
      source_class: c.source_class,
      controlled: c.controlled,
      as_of: c.as_of,
      available: c.available,
      locator: c.locator
        ? {
            page: c.locator.page,
            section: c.locator.section,
            field: c.locator.field
          }
        : null,
      applicability: c.applicability,
      evidence_set_id: c.evidence_set_id,
      calculation: c.calculation
    }))
  };
}

/** Identical provenance text in the rendered panel and answer copy/export. */
export function sourceObservationLines(
  observation: SourceObservation
): string[] {
  if ('state' in observation)
    return [t`Source unavailable under current access or source state.`];
  const lines = [
    observation.recorded_only
      ? observation.returned_count === 1
        ? t`Recorded history · 1 sample returned`
        : t`Recorded history · ${observation.returned_count} samples returned`
      : t`One source record observed`,
    t`Full population unknown · revision completeness unknown`,
    t`Captured: ${observation.captured_at}`
  ];
  if (observation.source_evidence_kind === 'synthetic_fixture')
    lines.push(t`Synthetic fixture`);
  if (observation.window) {
    const start = new Date(observation.window[0]).toISOString();
    const end = new Date(observation.window[1]).toISOString();
    lines.push(t`Recorded window (UTC): ${start} to ${end} (end excluded)`);
    lines.push(
      observation.window_complete
        ? t`Selected window complete`
        : t`Selected window incomplete`
    );
  }
  for (const gap of observation.gaps) {
    const start = new Date(gap.window[0]).toISOString();
    const end = new Date(gap.window[1]).toISOString();
    lines.push(t`Recorded gap: ${start} to ${end} · ${gap.reason}`);
  }
  return lines;
}

export function composeSourceAnswerMarkdown(
  content: string,
  observation: SourceObservation
): string {
  const sources =
    'state' in observation
      ? []
      : observation.citations.map((c) =>
          formatCitationLine(sourceCitationDisplay(c))
        );
  return [content, ...sourceObservationLines(observation), ...sources].join(
    '\n\n'
  );
}
