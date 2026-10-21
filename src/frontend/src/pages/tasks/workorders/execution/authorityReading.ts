/** Preserve recorded zero/false and unknowns without inventing a run encoding. */
export function authorityReading(value: unknown): string | null {
  const scalar =
    value && typeof value === 'object' && 'value' in value
      ? value.value
      : value;
  if (typeof scalar === 'number')
    return Number.isFinite(scalar) ? String(scalar) : null;
  if (typeof scalar === 'boolean') return String(scalar);
  if (typeof scalar === 'string' && scalar.trim()) return scalar;
  return null;
}
