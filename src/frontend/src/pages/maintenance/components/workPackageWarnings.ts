export const ASSIGNMENT_WARNING_CODES = [
  'ASSIGNMENT_UNASSIGNED',
  'ASSIGNMENT_QUALIFIED',
  'ASSIGNMENT_REQUIREMENTS_UNKNOWN',
  'ASSIGNMENT_MANUAL_OVERRIDE',
  'ASSIGNMENT_PERSON_UNAVAILABLE',
  'ASSIGNMENT_REQUIREMENTS_INVALID',
  'ASSIGNMENT_TRADE_MISMATCH',
  'ASSIGNMENT_AMBIGUOUS',
  'ASSIGNMENT_NO_QUALIFIED_MATCH'
] as const;

export type AssignmentWarningCode =
  | (typeof ASSIGNMENT_WARNING_CODES)[number]
  | 'ASSIGNMENT_REVIEW_REQUIRED';

export function assignmentWarningCode(
  warning: string
): AssignmentWarningCode | null {
  const match = /^Assignment review required: (ASSIGNMENT_[A-Z_]+)$/.exec(
    warning
  );
  if (!match) return null;
  return (
    ASSIGNMENT_WARNING_CODES.find((code) => code === match[1]) ??
    'ASSIGNMENT_REVIEW_REQUIRED'
  );
}

export function assignmentWarningNeedsReview(
  code: AssignmentWarningCode | null
) {
  return (
    code !== 'ASSIGNMENT_MANUAL_OVERRIDE' && code !== 'ASSIGNMENT_QUALIFIED'
  );
}
