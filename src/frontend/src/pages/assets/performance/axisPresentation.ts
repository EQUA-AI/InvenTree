/** Never hide a supplied negative value; floor only known non-negative axes with non-negative data. */
export function nonNegativeUnit(unit: string) {
  return /^(?:MW|kW|W|rpm|A|V|Hz|cusec|%|percent|m3\/h|m³\/h|m\^?3\/s|m³\/s)$/i.test(
    unit.trim()
  );
}
export function presentationTicks(
  ticks: number[],
  min: number,
  unit: string,
  count = false
): number[] {
  if (!ticks.length) return ticks;
  let result =
    min >= 0 && (count || nonNegativeUnit(unit))
      ? [0, ...ticks.filter((tick) => tick > 0)]
      : [...ticks];
  if (count) result = [...new Set(result.map((tick) => Math.round(tick)))];
  if (result.length < 2) result.push(result[0] + 1);
  return result;
}
