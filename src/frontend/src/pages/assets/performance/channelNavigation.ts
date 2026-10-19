import type { ResolvedParameter } from './resolve';
import type { SeriesTargets } from './usePerformanceData';

/** Mirrors the existing series reader's MAX_SERIES_TARGETS contract. */
export const MAX_SERIES_TARGETS = 200;
export const PERFORMANCE_SECTIONS = [
  'electrical',
  'operation',
  'winding',
  'vibration',
  'cooling',
  'bearing',
  'other'
] as const;
export type PerformanceSection = (typeof PERFORMANCE_SECTIONS)[number];

export function sectionOf(parameter: ResolvedParameter): PerformanceSection {
  const category = parameter.definition.category;
  return category === 'hydraulic' || category === 'station'
    ? 'operation'
    : category;
}

export function matchesChannel(
  parameter: ResolvedParameter,
  search: string
): boolean {
  const text = search.trim().toLocaleLowerCase();
  return (
    !text ||
    [
      parameter.label,
      parameter.signal.display_name,
      parameter.signal.external_key,
      parameter.signal.unit
    ].some((value) => value.toLocaleLowerCase().includes(text))
  );
}

/** Stable aggregate scope; display filtering never changes the read identity. */
export function seriesBatches(
  bindings: readonly number[],
  keys: readonly string[]
): SeriesTargets[] {
  const targets = [
    ...[...new Set(bindings)]
      .sort((a, b) => a - b)
      .map((binding) => ({ binding })),
    ...[...new Set(keys)].sort().map((key) => ({ key }))
  ];
  const batches: SeriesTargets[] = [];
  for (let index = 0; index < targets.length; index += MAX_SERIES_TARGETS) {
    const slice = targets.slice(index, index + MAX_SERIES_TARGETS);
    batches.push({
      bindings: slice.flatMap((target) =>
        'binding' in target ? [target.binding] : []
      ),
      keys: slice.flatMap((target) => ('key' in target ? [target.key] : []))
    });
  }
  return batches;
}
