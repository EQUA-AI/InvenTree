/** Preserve field paths and scalar values without printing JSON to the operator. */
export function deviationFacts(
  value: unknown,
  path = ''
): { field: string; value: string | number | boolean | null }[] {
  if (value === undefined) return [];
  if (value !== null && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, item]) => {
      const label = Array.isArray(value)
        ? String(Number(key) + 1)
        : key.replaceAll('_', ' ');
      return deviationFacts(item, path ? `${path} · ${label}` : label);
    });
  }
  return [
    {
      field: path,
      value:
        typeof value === 'string' ||
        typeof value === 'number' ||
        typeof value === 'boolean'
          ? value
          : null
    }
  ];
}
