import type { PackageScope } from '@lib/types/WorkExecution';

export interface ScopeTextChange {
  sequence: number;
  before: string | null;
  after: string | null;
  state: 'added' | 'removed' | 'changed' | 'same';
}

/** Ordinal text comparison only: a line number is not an execution-task id. */
export function compareScopeText(
  before: PackageScope['lines'],
  after: PackageScope['lines']
): ScopeTextChange[] {
  const old = new Map(before.map((line) => [line.sequence, line.action]));
  const next = new Map(after.map((line) => [line.sequence, line.action]));
  return [...new Set([...old.keys(), ...next.keys()])]
    .sort((a, b) => a - b)
    .map((sequence) => ({
      sequence,
      before: old.get(sequence) ?? null,
      after: next.get(sequence) ?? null,
      state: !old.has(sequence)
        ? 'added'
        : !next.has(sequence)
          ? 'removed'
          : old.get(sequence) === next.get(sequence)
            ? 'same'
            : 'changed'
    }));
}

/** An obsolete or conflicting row must never be selected as effective scope. */
export function effectiveScope(scopes: PackageScope[], id: number | null) {
  const current = scopes.filter((scope) => scope.current);
  const latest = Math.max(...scopes.map((scope) => scope.version));
  return current.length === 1 &&
    current[0].id === id &&
    current[0].version === latest
    ? current[0]
    : null;
}
