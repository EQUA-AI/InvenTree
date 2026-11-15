/**
 * S11: pure formatters shared by the evidence UI AND copy/export.
 *
 * Displayed coverage/citation text and exported text come from the SAME
 * functions over the SAME persisted manifest fields, so live copy ≡ reload
 * copy ≡ export is a function-identity property (Q83), never a scrape of
 * rendered prose.
 */

import type { RetrievalCoveragePayload } from '@lib/types/AimmsWire.generated';
import { t } from '@lingui/core/macro';
import type { EvidenceAnalysisAttachment } from './evidenceAnalysis';

export {
  citationDisplayFromManifest,
  formatCitationLine
} from './citationFormat';
import {
  citationDisplayFromManifest,
  formatCitationLine
} from './citationFormat';

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
