import type { CloseoutProposal } from '@lib/types/Closeout';

/** Saved human decisions take precedence over the extraction, including rejection. */
export function savedFieldValue(
  proposal: CloseoutProposal | null | undefined,
  field: string
): string {
  const decision = proposal?.decisions.find((row) => row.field_path === field);
  if (decision)
    return decision.decision === 'rejected'
      ? ''
      : String(decision.final_value ?? '');
  return String(proposal?.fields[field]?.value ?? '');
}

/** The server accepts an unchanged extraction without an edited final_value. */
export function fieldDecisions(
  proposal: CloseoutProposal | null | undefined,
  values: Record<string, string>
) {
  return Object.entries(values)
    .filter(([, value]) => value.trim())
    .map(([field_path, value]) =>
      proposal?.fields[field_path]?.value != null &&
      String(proposal.fields[field_path].value) === value
        ? { field_path, decision: 'accepted' as const }
        : { field_path, decision: 'edited' as const, final_value: value }
    );
}
