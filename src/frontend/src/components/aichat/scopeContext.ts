/**
 * Pure derivation of the drawer's read-only analysis context (plan C2).
 *
 * The context line reports what the SERVER confirmed; it never infers a
 * scope from a page path, a routing hint, or a local selection. Labels are
 * resolved here as data; user-facing translation happens in the component.
 */

export interface ScopeLike {
  mode: string;
  displayLabel?: string | null;
  machineCount?: number | null;
}

export type AnalysisContext =
  | { kind: 'unconfirmed' }
  | { kind: 'fleet' }
  | { kind: 'explicit'; label: string | null; machineCount: number | null };

export function analysisContext(
  scope: ScopeLike | null | undefined
): AnalysisContext {
  if (
    !scope ||
    scope.mode === 'legacy_unconfirmed' ||
    scope.mode === 'site_group'
  ) {
    return { kind: 'unconfirmed' };
  }
  if (scope.mode === 'all_authorized_assets') {
    return { kind: 'fleet' };
  }
  if (scope.mode === 'explicit_assets') {
    const label =
      scope.displayLabel && scope.displayLabel.trim() !== ''
        ? scope.displayLabel
        : null;
    const count =
      typeof scope.machineCount === 'number' ? scope.machineCount : null;
    if (!label && (count === null || count <= 0)) {
      // Nothing names or counts the selection: do not invent a context.
      return { kind: 'unconfirmed' };
    }
    return { kind: 'explicit', label, machineCount: count };
  }
  return { kind: 'unconfirmed' };
}

/**
 * True when the confirmed scope already covers the transient machine hint,
 * so the redundant "Asking about …" chip can collapse. This is a DISPLAY
 * decision only (matching the confirmed display label against the hint
 * name); authorization is and stays server-side.
 */
export function hintIsRedundant(
  scope: ScopeLike | null | undefined,
  hint: { machineName: string } | null | undefined
): boolean {
  if (!hint) return false;
  const context = analysisContext(scope);
  return (
    context.kind === 'explicit' &&
    context.label !== null &&
    context.label === hint.machineName
  );
}
