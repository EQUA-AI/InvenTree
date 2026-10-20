import type {
  AuthorityRecord,
  OperationalAuthority
} from '@lib/types/OperationalAuthority';
import { describe, expect, it } from 'vitest';
import { acceptanceSummary } from './authorityProjection';
const now = Date.parse('2026-10-20T12:00:00Z');
const row = {
  kind: 'test_acceptance',
  current: true,
  outcome: 'accepted',
  basis_state: 'current',
  effective_state: 'within_window',
  effective_from: '2026-10-20T10:00:00Z',
  effective_until: '2026-10-20T14:00:00Z'
} as AuthorityRecord;
const value = (rows: AuthorityRecord[]) =>
  ({
    rule: 'operational_authority.v1',
    operational: rows,
    physical: []
  }) as unknown as OperationalAuthority;
describe('separate acceptance outcome, source basis and effective window', () => {
  it('distinguishes unavailable provider, no current record and superseded history', () => {
    expect(acceptanceSummary(undefined, now).state).toBe('unavailable');
    expect(acceptanceSummary(value([]), now).state).toBe('not_recorded');
    expect(
      acceptanceSummary(value([{ ...row, current: false }]), now).state
    ).toBe('not_recorded');
  });
  it('retains rejection rather than claiming an accepted or generic recorded state', () => {
    expect(
      acceptanceSummary(value([{ ...row, outcome: 'rejected' }]), now).outcomes
    ).toEqual(['rejected']);
  });
  it('marks changed and unknown bases independently of an authored acceptance', () => {
    for (const basis_state of ['changed_or_unrecorded', undefined] as const) {
      const result = acceptanceSummary(value([{ ...row, basis_state }]), now);
      expect(result.outcomes).toEqual(['accepted']);
      expect(result.basis).toBe(true);
    }
  });
  it('does not promote expired or future acceptance windows', () => {
    for (const changed of [
      { effective_until: '2026-10-20T11:00:00Z' },
      { effective_from: '2026-10-20T13:00:00Z' },
      { effective_state: 'awaiting_verification' }
    ])
      expect(
        acceptanceSummary(value([{ ...row, ...changed }]), now).ineffective
      ).toBe(true);
  });
  it('preserves multiple conflicting current outcomes without choosing success', () => {
    expect(
      acceptanceSummary(value([row, { ...row, outcome: 'rejected' }]), now)
        .outcomes
    ).toEqual(['accepted', 'rejected']);
  });
});
