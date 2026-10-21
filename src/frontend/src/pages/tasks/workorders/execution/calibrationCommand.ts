import type { CalibrationTraceability } from '@lib/types/RoutineWork';

/** A pointer or old provider response cannot establish the recorded bytes. */
export function recordedCertificateAvailable(
  row?: Pick<
    CalibrationTraceability,
    'certificate_available' | 'certificate_url' | 'certificate_integrity_state'
  >
): boolean {
  return (
    row?.certificate_available === true &&
    row.certificate_integrity_state === 'unchanged' &&
    !!row.certificate_url
  );
}

/** Refuse an unconfirmed or foreign response before presenting a saved revision. */
export function savedCalibrationRevision(
  response: unknown,
  workId: number,
  readingId: number,
  previousRevision: number
): boolean {
  if (!response || typeof response !== 'object') return false;
  const row = (response as { traceability?: unknown }).traceability;
  if (!row || typeof row !== 'object') return false;
  const value = row as Record<string, unknown>;
  return (
    Number.isSafeInteger(value.id) &&
    Number(value.id) > 0 &&
    value.work_order === workId &&
    value.reading === readingId &&
    value.revision === previousRevision + 1 &&
    Number.isSafeInteger(value.recorded_by) &&
    Number(value.recorded_by) > 0 &&
    typeof value.recorded_at === 'string' &&
    Number.isFinite(Date.parse(value.recorded_at))
  );
}
