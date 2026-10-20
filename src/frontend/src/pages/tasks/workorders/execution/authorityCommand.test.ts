import { describe, expect, it } from 'vitest';
import {
  type AuthorityIntent,
  type AuthorityReceipt,
  authorityDraftContext,
  authorityIntentIsStale,
  matchingAuthorityReceipt,
  restoreAuthorityCommand
} from './authorityCommand';
const intent: AuthorityIntent = {
  workOrderId: 73,
  actorId: 1,
  payload: {
    operation: 'record',
    expected_version: 7,
    expected_context: 'a'.repeat(64),
    idempotency_key: 'authority-1',
    fields: {
      kind: 'permit',
      outcome: 'active',
      statement: 'Permit P',
      source_reference: 'Site P'
    }
  }
};
const receipt: AuthorityReceipt = {
  work_order_id: 73,
  work_version: 8,
  idempotency_key: 'authority-1',
  expected_context: 'a'.repeat(64),
  record: {
    recorded_by: 'Recorder',
    recorded_by_id: 1,
    recorded_at: '2026-10-19T08:00:00Z',
    supersedes: null,
    id: 9,
    version: 8,
    work_order_id: 73,
    kind: 'permit',
    outcome: 'active',
    statement: 'Permit P',
    source_reference: 'Site P',
    observed_at: '2026-10-19T08:00:00Z',
    effective_from: '2026-10-19T08:00:00Z',
    effective_until: null,
    effective_state: 'within_window',
    basis_state: 'current',
    current: true,
    gate_id: 4,
    gate_name: 'Synthetic gate',
    target_id: null,
    target_name: null,
    verified_by: null,
    verified_by_id: null,
    verified_at: null,
    details: {},
    basis: {}
  }
};
describe('operational authority command recovery', () => {
  it('rejects changed asset, expiry, observation and service details in a success body', () => {
    const service: AuthorityIntent = {
      ...intent,
      payload: {
        ...intent.payload,
        fields: {
          kind: 'service_state',
          outcome: 'operator_recorded',
          statement: 'Operator context',
          source_reference: 'Log',
          target_id: 18,
          observed_at: '2026-10-19T08:00:00Z',
          effective_from: '2026-10-19T08:00:00Z',
          effective_until: null,
          duty: 'Assigned duty',
          standby: '',
          binding_id: 601,
          telemetry_observed_at: '2026-10-19T08:00:00Z',
          telemetry_token: 'c'.repeat(64)
        }
      }
    };
    const saved = {
      ...receipt,
      record: {
        ...receipt.record,
        kind: 'service_state',
        outcome: 'operator_recorded',
        statement: 'Operator context',
        source_reference: 'Log',
        target_id: 18,
        observed_at: '2026-10-19T08:00:00+00:00',
        effective_from: '2026-10-19T08:00:00+00:00',
        effective_until: null,
        details: { duty: 'Assigned duty', standby: null },
        basis: {
          telemetry: {
            binding_id: 601,
            observed_at: '2026-10-19T08:00:00Z',
            reading_token: 'c'.repeat(64)
          }
        }
      }
    };
    expect(matchingAuthorityReceipt(saved, service)).toBe(true);
    expect(
      matchingAuthorityReceipt(
        { ...saved, record: { ...saved.record, details: undefined } },
        service
      )
    ).toBe(false);
    for (const change of [
      { target_id: 19 },
      { effective_until: '2026-10-20T08:00:00Z' },
      { observed_at: '2026-10-19T09:00:00Z' },
      { details: { duty: 'Different duty', standby: null } },
      { recorded_by_id: 99 },
      {
        basis: {
          telemetry: {
            binding_id: 601,
            observed_at: '2026-10-19T08:00:00Z',
            reading_token: 'd'.repeat(64)
          }
        }
      }
    ])
      expect(
        matchingAuthorityReceipt(
          { ...saved, record: { ...saved.record, ...change } },
          service
        )
      ).toBe(false);
    expect(
      restoreAuthorityCommand(
        JSON.stringify({ status: 'pending', intent }),
        73,
        99
      )
    ).toEqual({ status: 'blocked' });
  });
  it('accepts only the matching owner, version, key and attributed fact', () => {
    expect(matchingAuthorityReceipt(receipt, intent)).toBe(true);
    for (const change of [
      { work_order_id: 74 },
      { work_version: 9 },
      { idempotency_key: 'other' },
      { expected_context: 'b'.repeat(64) },
      { record: { ...receipt.record, kind: 'operating_permission' } },
      { record: { ...receipt.record, statement: 'Other permit' } },
      { record: { ...receipt.record, outcome: 'revoked' } }
    ])
      expect(matchingAuthorityReceipt({ ...receipt, ...change }, intent)).toBe(
        false
      );
  });
  it('reload keeps one original pending operation and fails closed on unreadable state', () => {
    expect(restoreAuthorityCommand(null, 73)).toEqual({ status: 'idle' });
    expect(restoreAuthorityCommand('{broken', 73)).toEqual({
      status: 'blocked'
    });
    expect(
      restoreAuthorityCommand(JSON.stringify({ status: 'pending', intent }), 73)
    ).toEqual({ status: 'uncertain', intent });
    expect(
      restoreAuthorityCommand(JSON.stringify({ status: 'pending', intent }), 74)
    ).toEqual({ status: 'blocked' });
  });
  it('an unrelated saved receipt remains uncertain after refresh', () => {
    expect(
      restoreAuthorityCommand(
        JSON.stringify({
          status: 'confirmed',
          intent,
          receipt: { ...receipt, work_version: 9 }
        }),
        73
      )
    ).toEqual({ status: 'uncertain', intent });
    expect(
      restoreAuthorityCommand(
        JSON.stringify({ status: 'confirmed', intent, receipt }),
        73
      )
    ).toMatchObject({ status: 'confirmed' });
  });
  it('verification needs the exact source record and a second attribution', () => {
    const verify: AuthorityIntent = {
      workOrderId: 73,
      actorId: 1,
      payload: {
        ...intent.payload,
        operation: 'verify',
        fields: { gate_id: 4 },
        supersedes: 3
      }
    };
    expect(matchingAuthorityReceipt(receipt, verify)).toBe(false);
    const verified = {
      ...receipt,
      record: {
        ...receipt.record,
        supersedes: 3,
        gate_id: 4,
        recorded_by_id: 999,
        verified_by_id: 1,
        verified_by: 'Other',
        verified_at: '2026-10-19T08:00:00Z'
      }
    };
    expect(matchingAuthorityReceipt(verified, verify)).toBe(true);
    expect(
      matchingAuthorityReceipt(
        { ...verified, record: { ...verified.record, supersedes: 2 } },
        verify
      )
    ).toBe(false);
  });
});

describe('mounted sibling authority drafts', () => {
  it('identifies a permit draft older than a confirmed maintenance receipt', () => {
    const saved = {
      status: 'confirmed' as const,
      intent,
      receipt: { ...receipt, work_version: 8 }
    };
    expect(authorityIntentIsStale(saved, intent)).toBe(true);
    expect(
      authorityIntentIsStale(saved, {
        ...intent,
        payload: { ...intent.payload, expected_version: 8 }
      })
    ).toBe(false);
  });
  it('repins on overview version advance, retaining same-version source drift for review', () => {
    const pinned = { version: 7, token: 'a'.repeat(64) };
    expect(authorityDraftContext(pinned, 8, 'b'.repeat(64))).toEqual({
      version: 8,
      token: 'b'.repeat(64)
    });
    expect(authorityDraftContext(pinned, 7, 'b'.repeat(64))).toEqual(pinned);
    expect(authorityDraftContext(null, 8, 'b'.repeat(64))).toBeNull();
  });
});
