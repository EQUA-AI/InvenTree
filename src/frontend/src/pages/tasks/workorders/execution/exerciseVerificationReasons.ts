export const exerciseReasonCodes = [
  'step_missing',
  'criteria_invalid',
  'current_pass_not_recorded',
  'value_does_not_match_prepared_evidence',
  'required_prepared_evidence_missing'
] as const;

export function normalizeExerciseReason(reason: string) {
  return exerciseReasonCodes.find((code) => code === reason) ?? 'unknown';
}

export const exerciseResultReasonCodes = [
  'published_procedure_not_applied',
  'procedure_revision_or_snapshot_invalid',
  'source_or_ownership_invalid'
] as const;

export function normalizeExerciseResultReason(reason: string) {
  return exerciseResultReasonCodes.find((code) => code === reason) ?? 'unknown';
}
