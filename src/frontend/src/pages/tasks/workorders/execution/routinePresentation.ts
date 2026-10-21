import type { RoutineWork } from '@lib/types/RoutineWork';
import { t } from '@lingui/core/macro';

export function routineStatusLabel(status: string): string {
  return (
    (
      {
        pending: t`Pending`,
        in_progress: t`In progress`,
        completed: t`Completed`,
        failed: t`Failed`,
        skipped: t`Skipped`,
        not_applicable: t`Not applicable`
      } as Record<string, string>
    )[status] ?? t`Status not recognized`
  );
}

/**
 * A parsed reading is not an accepted test or a physical clearance. The
 * backend reading states are pending, verified, failed and dispositioned.
 */
export function readingEvaluationLabel(
  reading: RoutineWork['readings'][number]
): string {
  if (reading.verification_state === 'dispositioned')
    return t`Disposition recorded · earlier outcome unavailable`;
  if (reading.verification_state === 'failed')
    return t`Failed reading · review warnings and recorded limits`;
  if (reading.verification_state === 'verified') {
    return reading.expected_min == null && reading.expected_max == null
      ? t`Value parsed · no limits recorded · not evaluated`
      : t`Within recorded limits · acceptance separate`;
  }
  return t`Not evaluated`;
}

/** Native Closeout does not supply comparison limits in this read model. */
export function readingStatusLabel(state: string): string {
  switch (state) {
    case 'verified':
      return t`Value parsed · acceptance separate`;
    case 'failed':
      return t`Failed reading`;
    case 'dispositioned':
      return t`Disposition recorded · earlier outcome unavailable`;
    case 'pending':
      return t`Not evaluated`;
    default:
      return t`Evaluation not supplied`;
  }
}
