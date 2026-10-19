/**
 * R6 P13: a labelled way back to where a native record was opened from.
 *
 * Carried in router state, so a native page (a Part, its Stock) can offer
 * "Back to Pump 01 · Parts" without a new route or query parameter. Only an
 * in-app path is accepted: state can come from anywhere in history.
 */
export interface ReturnPath {
  to: string;
  label: string;
}

export function returnState(to: string, label: string) {
  return { returnTo: { to, label } satisfies ReturnPath };
}

export function readReturnPath(state: unknown): ReturnPath | null {
  const candidate = (state as { returnTo?: Partial<ReturnPath> } | null)
    ?.returnTo;
  if (!candidate) return null;
  const { to, label } = candidate;
  if (typeof to !== 'string' || typeof label !== 'string') return null;
  // Same-app absolute path only: no scheme, host or protocol-relative URL.
  if (!to.startsWith('/') || to.startsWith('//') || to.includes('\\'))
    return null;
  if (!label.trim()) return null;
  return { to, label: label.trim() };
}
