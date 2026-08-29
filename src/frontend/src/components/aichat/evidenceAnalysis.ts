/**
 * S11 (WP-C1): the consolidated evidence-analysis attachment.
 *
 * ONE canonical payload, three envelopes: the legacy SSE
 * `STATE_DELTA {kind: "evidence_analysis"}`, the AG-UI
 * `aimms.evidenceAnalysis` CUSTOM channel, and the persisted message
 * `evidence_analysis` field on thread reload all carry this same object
 * shape. Every ingest path does envelope extraction only and then calls
 * `normalizeEvidenceAnalysis` — a fourth shape divergence cannot happen,
 * which is what makes live/reload/export fidelity (Q83) structural.
 *
 * The normalizer FAILS CLOSED: any structural surprise returns null and the
 * message renders exactly like a v1 message — never a partial evidence UI.
 */

import type {
  AnalysisNoDataReason,
  AnalysisProgressStage,
  CitationManifestEntry,
  ClaimPayload,
  RetrievalCoveragePayload
} from '@lib/types/AimmsWire.generated';

const CLASSIFICATIONS = new Set([
  'documented',
  'calculated',
  'inferred',
  'insufficient'
]);

const NO_DATA_REASONS = new Set([
  'complete_population_no_matches',
  'outside_active_selection',
  'unauthorized_or_unavailable',
  'retrieval_failure',
  'unresolved_applicability',
  'incomplete_coverage'
]);

export const ANALYSIS_PROGRESS_STAGES: readonly AnalysisProgressStage[] = [
  'confirming_scope',
  'reviewing_records',
  'validating_evidence'
];

/** The FE-side aggregate; primitives are the generated wire types. */
export interface EvidenceAnalysisAttachment {
  response_version: 2;
  response_state: 'complete' | 'partial';
  incomplete_reasons: { code: string; facet: string }[];
  no_data_reason: AnalysisNoDataReason | null;
  active_scope: { display_label: string; version: number } | null;
  claims: ClaimPayload[];
  citations: CitationManifestEntry[];
  coverage: RetrievalCoveragePayload | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function normalizeClaim(raw: unknown): ClaimPayload | null {
  if (!isRecord(raw)) return null;
  const classification = String(raw.evidence_classification ?? '');
  // Unknown classifications DROP the claim (the entity-chip filter
  // precedent): fail closed, never guess a display meaning.
  if (!CLASSIFICATIONS.has(classification)) return null;
  const ordinals = Array.isArray(raw.citation_ordinals)
    ? raw.citation_ordinals.filter(
        (value): value is number =>
          Number.isInteger(value) && (value as number) >= 1
      )
    : [];
  return {
    claim_id: String(raw.claim_id ?? ''),
    claim_role: String(raw.claim_role ?? ''),
    claim_type: String(raw.claim_type ?? ''),
    evidence_classification:
      classification as ClaimPayload['evidence_classification'],
    citation_ordinals: ordinals,
    entity_refs: Array.isArray(raw.entity_refs)
      ? raw.entity_refs.map((value) => String(value))
      : []
  };
}

function normalizeCitation(raw: unknown): CitationManifestEntry | null {
  if (!isRecord(raw)) return null;
  const ordinal = raw.ordinal;
  if (!Number.isInteger(ordinal) || (ordinal as number) < 1) return null;
  const locator = isRecord(raw.locator)
    ? {
        page: Number.isInteger(raw.locator.page)
          ? (raw.locator.page as number)
          : null,
        section:
          raw.locator.section == null ? null : String(raw.locator.section),
        field: raw.locator.field == null ? null : String(raw.locator.field)
      }
    : null;
  return {
    ordinal: ordinal as number,
    source_type: String(raw.source_type ?? ''),
    source_id: raw.source_id == null ? null : String(raw.source_id),
    source_title: raw.source_title == null ? null : String(raw.source_title),
    source_revision:
      raw.source_revision == null ? null : String(raw.source_revision),
    source_class: raw.source_class == null ? null : String(raw.source_class),
    controlled: raw.controlled === true,
    as_of: String(raw.as_of ?? ''),
    available: raw.available !== false,
    locator,
    applicability: raw.applicability == null ? null : String(raw.applicability),
    evidence_set_id:
      raw.evidence_set_id == null ? null : String(raw.evidence_set_id),
    calculation: raw.calculation == null ? null : String(raw.calculation)
  };
}

function normalizeCoverage(raw: unknown): RetrievalCoveragePayload | null {
  if (!isRecord(raw)) return null;
  if (
    !Number.isInteger(raw.population_count) ||
    !Number.isInteger(raw.returned_count)
  ) {
    return null;
  }
  return {
    population_count: raw.population_count as number,
    returned_count: raw.returned_count as number,
    complete_population: raw.complete_population === true,
    display_truncated: raw.display_truncated === true,
    date_field: raw.date_field == null ? null : String(raw.date_field),
    timezone: raw.timezone == null ? null : String(raw.timezone),
    filters: Array.isArray(raw.filters)
      ? raw.filters.map((value) => String(value))
      : [],
    as_of: String(raw.as_of ?? ''),
    snapshot_label:
      raw.snapshot_label == null ? null : String(raw.snapshot_label),
    excluded_null_date_count: Number.isInteger(raw.excluded_null_date_count)
      ? (raw.excluded_null_date_count as number)
      : null,
    incomplete_reason:
      raw.incomplete_reason == null ? null : String(raw.incomplete_reason)
  };
}

/**
 * Normalize one raw evidence-analysis payload from ANY envelope.
 * Returns null on structural failure — the caller renders v1-style.
 */
export function normalizeEvidenceAnalysis(
  raw: unknown
): EvidenceAnalysisAttachment | null {
  try {
    if (!isRecord(raw)) return null;
    if (raw.response_version !== 2) return null;
    const state = raw.response_state === 'partial' ? 'partial' : 'complete';
    if (raw.response_state !== 'complete' && raw.response_state !== 'partial') {
      return null;
    }
    if (!Array.isArray(raw.claims) || !Array.isArray(raw.citations))
      return null;
    const claims = raw.claims
      .map(normalizeClaim)
      .filter((claim): claim is ClaimPayload => claim !== null);
    const citations = raw.citations
      .map(normalizeCitation)
      .filter((entry): entry is CitationManifestEntry => entry !== null);
    const rawReason = raw.no_data_reason;
    const noDataReason =
      typeof rawReason === 'string' && NO_DATA_REASONS.has(rawReason)
        ? (rawReason as AnalysisNoDataReason)
        : null;
    const scope = isRecord(raw.active_scope)
      ? {
          display_label: String(raw.active_scope.display_label ?? ''),
          version: Number.isInteger(raw.active_scope.version)
            ? (raw.active_scope.version as number)
            : 0
        }
      : null;
    const reasons = Array.isArray(raw.incomplete_reasons)
      ? raw.incomplete_reasons.filter(isRecord).map((entry) => ({
          code: String(entry.code ?? ''),
          facet: String(entry.facet ?? '')
        }))
      : [];
    return {
      response_version: 2,
      response_state: state,
      incomplete_reasons: reasons,
      no_data_reason: noDataReason,
      active_scope: scope,
      claims,
      citations,
      coverage: normalizeCoverage(raw.coverage)
    };
  } catch {
    return null;
  }
}

/** Progress stages are a CLOSED enum: anything else never paints. */
export function normalizeProgressStage(
  raw: unknown
): AnalysisProgressStage | null {
  return typeof raw === 'string' &&
    (ANALYSIS_PROGRESS_STAGES as readonly string[]).includes(raw)
    ? (raw as AnalysisProgressStage)
    : null;
}
