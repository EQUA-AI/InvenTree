/**
 * S11: pure formatters shared by the evidence UI AND copy/export.
 *
 * Displayed coverage/citation text and exported text come from the SAME
 * functions over the SAME persisted manifest fields, so live copy ≡ reload
 * copy ≡ export is a function-identity property (Q83), never a scrape of
 * rendered prose.
 */

import type {
  CitationManifestEntry,
  RetrievalCoveragePayload
} from '@lib/types/AimmsWire.generated';
import { t } from '@lingui/core/macro';
import { type CitationDisplayEntry, formatLocator } from './CitationList';
import type { EvidenceAnalysisAttachment } from './evidenceAnalysis';

/** Manifest entry → display row (the ONE mapping every surface uses). */
export function citationDisplayFromManifest(
  entry: CitationManifestEntry
): CitationDisplayEntry {
  return {
    ordinal: entry.ordinal,
    sourceType: entry.source_type,
    available: entry.available,
    asOf: entry.as_of,
    sourceId: entry.source_id ?? undefined,
    sourceTitle: entry.source_title ?? undefined,
    sourceRevision: entry.source_revision ?? undefined,
    controlled: entry.controlled,
    sourceClass: entry.source_class ?? undefined,
    locator: entry.locator ?? undefined,
    applicability: entry.applicability ?? undefined,
    evidenceSetId: entry.evidence_set_id ?? undefined,
    calculation: entry.calculation ?? undefined
  };
}

/**
 * The coverage sentence. Display truncation and incomplete evaluation use
 * DISTINCT language (§8.8): "all N evaluated; M shown" is fine, "X of N
 * evaluated" carries the incomplete warning via formatCoverageWarning.
 */
export function formatCoverageLine(coverage: RetrievalCoveragePayload): string {
  const population = coverage.population_count;
  const returned = coverage.returned_count;
  if (coverage.complete_population) {
    if (coverage.display_truncated) {
      return t`All ${population} records evaluated; showing ${returned} of the full result`;
    }
    return t`${population}/${population} records evaluated · ${returned} shown`;
  }
  return t`${returned} of ${population} records evaluated`;
}

/** The incomplete-coverage warning, or null when coverage is complete. */
export function formatCoverageWarning(
  coverage: RetrievalCoveragePayload
): string | null {
  if (coverage.complete_population) return null;
  const base = t`Incomplete coverage: ${coverage.returned_count} of ${coverage.population_count} records evaluated`;
  return coverage.incomplete_reason
    ? `${base} (${coverage.incomplete_reason})`
    : base;
}

/** The null-date exclusion sentence (the no-silent-fallback surface). */
export function excludedLabel(count: number, dateField: string | null): string {
  const field = dateField ?? t`date`;
  return t`${count} records excluded (missing ${field})`;
}

/** One plain-text source line for copy/export; mirrors the display row. */
export function formatCitationLine(entry: CitationDisplayEntry): string {
  if (!entry.available) {
    return `[${entry.ordinal}] ${t`Source unavailable`}`;
  }
  const parts: string[] = [];
  const label = entry.sourceTitle || entry.sourceId || entry.sourceType;
  parts.push(label);
  if (
    entry.sourceTitle &&
    entry.sourceId &&
    entry.sourceTitle !== entry.sourceId
  ) {
    parts.push(entry.sourceId);
  }
  if (entry.sourceRevision) parts.push(t`rev ${entry.sourceRevision}`);
  const locator = formatLocator(entry.locator);
  if (locator) parts.push(locator);
  if (entry.controlled === false) parts.push(t`uncontrolled attachment`);
  if (entry.calculation) parts.push(entry.calculation);
  const stamp = entry.asOf ? ` — ${t`as of`} ${entry.asOf}` : '';
  return `[${entry.ordinal}] ${parts.join(' · ')}${stamp}`;
}

/**
 * The canonical copy/export composition (Q28): answer + scope + as-of +
 * coverage + limitations + numbered sources — from persisted manifest
 * fields ONLY, never from rendered DOM.
 */
export function composeAnswerMarkdown(
  content: string,
  attachment: EvidenceAnalysisAttachment
): string {
  const lines: string[] = [content.trimEnd(), '', '---'];
  if (attachment.active_scope) {
    lines.push(
      `**${t`Scope`}:** ${attachment.active_scope.display_label} (v${attachment.active_scope.version})`
    );
  }
  const coverage = attachment.coverage;
  if (coverage) {
    const asOfParts = [coverage.as_of];
    if (coverage.date_field) asOfParts.push(coverage.date_field);
    if (coverage.timezone) asOfParts.push(`(${coverage.timezone})`);
    lines.push(`**${t`As of`}:** ${asOfParts.filter(Boolean).join(' · ')}`);
    lines.push(`**${t`Coverage`}:** ${formatCoverageLine(coverage)}`);
  }
  const limitations: string[] = [];
  if (attachment.response_state === 'partial') {
    limitations.push(t`Partial answer — some parts did not finish in time.`);
  }
  for (const reason of attachment.incomplete_reasons) {
    limitations.push(`${reason.facet}: ${reason.code}`);
  }
  if (coverage) {
    const warning = formatCoverageWarning(coverage);
    if (warning) limitations.push(warning);
    if ((coverage.excluded_null_date_count ?? 0) > 0) {
      limitations.push(
        excludedLabel(
          coverage.excluded_null_date_count ?? 0,
          coverage.date_field
        )
      );
    }
  }
  lines.push(
    `**${t`Limitations`}:** ${limitations.length ? limitations.join('; ') : t`None noted`}`
  );
  if (attachment.citations.length) {
    lines.push('', `**${t`Sources`}**`);
    for (const entry of attachment.citations) {
      lines.push(formatCitationLine(citationDisplayFromManifest(entry)));
    }
  }
  return lines.join('\n');
}
