import type {
  InvestigationArtifact,
  InvestigationRequest
} from '@lib/types/WorkExecution';

export interface ObservationIntent {
  operation: 'artifact';
  expected_version: number;
  request_version: number;
  idempotency_key: string;
  fields: {
    kind: 'observation';
    payload: { text: string; observed_at?: string; conditions?: string };
    attachment_id?: number;
  };
}

/** A 2xx alone is insufficient: require the exact saved request and artifact. */
export function observationReceipt(
  data: unknown,
  workId: number,
  requestId: number,
  intent: ObservationIntent
): { request: InvestigationRequest; artifact: InvestigationArtifact } | null {
  if (!data || typeof data !== 'object') return null;
  const receipt = data as {
    request?: InvestigationRequest;
    artifact_id?: number;
  };
  const request = receipt.request;
  if (
    !request ||
    request.id !== requestId ||
    request.work_order !== workId ||
    !Number.isSafeInteger(request.version) ||
    request.version <= intent.request_version ||
    !Array.isArray(request.artifacts)
  )
    return null;
  const artifact = request.artifacts.find(
    (row) => row.id === receipt.artifact_id
  );
  if (
    !artifact ||
    !Number.isSafeInteger(artifact.id) ||
    artifact.id <= 0 ||
    artifact.request !== requestId ||
    artifact.kind !== 'observation' ||
    !Number.isSafeInteger(artifact.recorded_by) ||
    !artifact.created_at ||
    (artifact.attachment ?? null) !== (intent.fields.attachment_id ?? null) ||
    Object.entries(intent.fields.payload).some(
      ([key, value]) => artifact.payload?.[key] !== value
    )
  )
    return null;
  return { request, artifact };
}
