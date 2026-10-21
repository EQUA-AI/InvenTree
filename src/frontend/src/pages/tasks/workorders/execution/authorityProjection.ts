import type { OperationalAuthority } from '@lib/types/OperationalAuthority';

/** Current authored outcomes stay separate from their basis and effective window. */
export function acceptanceSummary(
  value?: OperationalAuthority,
  now = Date.now()
) {
  if (!value)
    return {
      state: 'unavailable' as const,
      outcomes: [],
      basis: false,
      ineffective: false
    };
  const rows = value.operational.filter(
    (row) => row.current && row.kind === 'test_acceptance'
  );
  return {
    state: rows.length ? ('recorded' as const) : ('not_recorded' as const),
    outcomes: [...new Set(rows.map((row) => row.outcome))].sort(),
    basis: rows.some((row) => row.basis_state !== 'current'),
    ineffective: rows.some(
      (row) =>
        row.effective_state !== 'within_window' ||
        Date.parse(row.effective_from) > now ||
        (row.effective_until !== null && Date.parse(row.effective_until) <= now)
    )
  };
}
