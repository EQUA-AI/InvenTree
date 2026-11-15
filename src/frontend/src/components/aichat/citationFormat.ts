/** Pure citation display/export formatting, independent of viewer state. */
import type {
  CitationManifestEntry,
  EvidenceClassification
} from '@lib/types/AimmsWire.generated';
import { t } from '@lingui/core/macro';

/** One displayable citation row (v1-adapted or v2 manifest-derived). */
export interface CitationDisplayEntry {
  ordinal: number;
  sourceType: string;
  available: boolean;
  asOf: string;
  sourceId?: string;
  sourceTitle?: string;
  sourceRevision?: string;
  /** true => Controlled badge; false => Uncontrolled-attachment badge;
   *  undefined (v1) => no control-class badge. */
  controlled?: boolean;
  sourceClass?: string;
  locator?: {
    page?: number | null;
    section?: string | null;
    field?: string | null;
  };
  applicability?: string;
  classification?: EvidenceClassification;
  evidenceSetId?: string;
  calculation?: string;
}

/** Human locator string; segments only when present. */
export function formatLocator(
  locator: CitationDisplayEntry['locator']
): string | null {
  if (!locator) return null;
  const parts: string[] = [];
  if (locator.page != null) parts.push(t`p. ${locator.page}`);
  if (locator.section) parts.push(`§${locator.section}`);
  if (locator.field) parts.push(t`field: ${locator.field}`);
  return parts.length ? parts.join(' · ') : null;
}

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
