import { describe, expect, it } from 'vitest';
import { savedRequest } from '../../../../../tests/s4/workExecutionFixtures';
import {
  type ObservationIntent,
  observationReceipt
} from './observationCapture';

const intent: ObservationIntent = {
  operation: 'artifact',
  expected_version: 7,
  request_version: 3,
  idempotency_key: 'exact-original-key',
  fields: {
    kind: 'observation',
    payload: {
      text: 'Observed original fact',
      observed_at: '2026-10-20T08:00:00Z'
    },
    attachment_id: 901
  }
};
const receipt = () => ({
  artifact_id: 501,
  request: {
    ...savedRequest(),
    version: 4,
    artifacts: [
      {
        id: 501,
        request: 401,
        kind: 'observation' as const,
        payload: { ...intent.fields.payload },
        attachment: 901,
        supersedes: null,
        recorded_by: 11,
        recorded_by_name: 'Actual recorder',
        created_at: '2026-10-20T09:00:00Z'
      }
    ]
  }
});

describe('observation save confirmation', () => {
  it('requires the exact saved Work/request/evidence linkage and original text', () => {
    expect(observationReceipt(receipt(), 73, 401, intent)?.artifact.id).toBe(
      501
    );
  });
  it.each([
    'work',
    'request',
    'evidence',
    'text',
    'attachment',
    'time',
    'version',
    'kind'
  ] as const)('never calls a mismatched %s receipt saved', (field) => {
    const value = receipt();
    switch (field) {
      case 'work':
        value.request.work_order = 86;
        break;
      case 'request':
        value.request.id = 402;
        break;
      case 'evidence':
        value.artifact_id = 999;
        break;
      case 'text':
        value.request.artifacts[0].payload.text = 'Different fact';
        break;
      case 'attachment':
        value.request.artifacts[0].attachment = 902;
        break;
      case 'time':
        value.request.artifacts[0].payload.observed_at = '2026-10-20T09:00:00Z';
        break;
      case 'version':
        value.request.version = 3;
        break;
      case 'kind':
        Object.assign(value.request.artifacts[0], { kind: 'reading_set' });
        break;
    }
    expect(observationReceipt(value, 73, 401, intent)).toBeNull();
  });
  it.each([
    null,
    {},
    { status: 'ok' },
    { request: { id: 401, work_order: 73, version: 4 } }
  ])('treats incomplete 2xx bodies as uncertain', (value) => {
    expect(observationReceipt(value, 73, 401, intent)).toBeNull();
  });
});
