import type {
  EvidenceRelation,
  Investigation,
  InvestigationEvidence,
  InvestigationFinding
} from '@lib/types/WorkExecution';

export type InvestigationReading =
  | { kind: 'unsupported' }
  | { kind: 'no_packet'; requestsAvailable: boolean }
  | {
      kind: 'ready';
      investigation: Investigation;
      qualification: 'verified' | 'preliminary' | 'none';
      evidence: Record<EvidenceRelation, InvestigationEvidence[]>;
      disputed: InvestigationFinding[];
      requestsAvailable: boolean;
    };

const RELATIONS: EvidenceRelation[] = ['supports', 'contradicts', 'unknown'];

/**
 * Accept only `investigation.v1`.
 *
 * Qualification comes from the server's recorded verification, never from how
 * the explanation reads. An evidence item with an unrecognised relation is
 * kept as "relation not established" rather than dropped or promoted to
 * support.
 */
export function readInvestigation(value: unknown): InvestigationReading {
  if (!value || typeof value !== 'object') return { kind: 'unsupported' };
  const investigation = value as Partial<Investigation>;
  if (
    investigation.rule !== 'investigation.v1' ||
    !Array.isArray(investigation.findings) ||
    !investigation.requests
  )
    return { kind: 'unsupported' };
  const requestsAvailable = investigation.requests.available === true;
  if (!investigation.packet) return { kind: 'no_packet', requestsAvailable };
  const explanation = investigation.explanation ?? null;
  const evidence: Record<EvidenceRelation, InvestigationEvidence[]> = {
    supports: [],
    contradicts: [],
    unknown: []
  };
  for (const item of explanation?.evidence ?? []) {
    const relation = RELATIONS.includes(item.relation)
      ? item.relation
      : 'unknown';
    evidence[relation].push(item);
  }
  return {
    kind: 'ready',
    investigation: investigation as Investigation,
    qualification: !explanation?.likely_cause
      ? 'none'
      : explanation.preliminary
        ? 'preliminary'
        : 'verified',
    evidence,
    disputed: investigation.findings.filter(
      (finding) => finding.verification === 'disputed'
    ),
    requestsAvailable
  };
}

/** A value with its unit, keeping a recorded zero as zero and missing as null. */
export function measured(
  value: number | string | null | undefined,
  unit?: string | null
): string | null {
  if (value === null || value === undefined || value === '') return null;
  return unit ? `${value} ${unit}` : String(value);
}

/** The launching route for "Continue investigation", on the Diagnose panel. */
export function diagnoseReturnPath(pathname: string, search: string): string {
  const params = new URLSearchParams(search);
  params.set('work_panel', 'diagnose');
  return `${pathname}?${params.toString()}`;
}
