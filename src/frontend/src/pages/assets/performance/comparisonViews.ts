import type { ResolvedParameter } from './resolve';
import { scaleKey } from './units';

/** A compatible scale is necessary but not enough: keep engineering roles apart. */
export function comparisonGroups(parameters: readonly ResolvedParameter[]) {
  const groups = new Map<string, ResolvedParameter[]>();
  for (const parameter of parameters) {
    const role =
      parameter.definition.role === 'other'
        ? parameter.id
        : parameter.definition.role;
    const key = `${role}/${scaleKey(parameter.signal.unit)}`;
    groups.set(key, [...(groups.get(key) ?? []), parameter]);
  }
  return [...groups.entries()].map(([key, items]) => ({ key, items }));
}

export interface DisplayView {
  name: string;
  search: string;
  section: string;
  bindings: number[];
}
export function displayViewKey(host: string, actor: number, machine: number) {
  return `aimms-performance-views:v1:${encodeURIComponent(host)}:${actor}:${machine}`;
}
export function parseDisplayViews(raw: string | undefined): DisplayView[] {
  try {
    const data: unknown = JSON.parse(raw ?? '');
    if (!Array.isArray(data)) return [];
    return data
      .filter(
        (item): item is DisplayView =>
          item &&
          typeof item.name === 'string' &&
          item.name.trim().length > 0 &&
          item.name.length <= 80 &&
          typeof item.search === 'string' &&
          item.search.length <= 200 &&
          [
            'all',
            'electrical',
            'operation',
            'winding',
            'vibration',
            'cooling',
            'bearing',
            'other'
          ].includes(item.section) &&
          Array.isArray(item.bindings) &&
          item.bindings.every(
            (id: unknown) =>
              typeof id === 'number' && Number.isSafeInteger(id) && id > 0
          )
      )
      .slice(0, 20)
      .map(({ name, search, section, bindings }) => ({
        name,
        search,
        section,
        bindings: [...new Set(bindings)].slice(0, 12)
      }))
      .filter(
        (view, index, all) =>
          all.findIndex((item) => item.name === view.name) === index
      );
  } catch {
    return [];
  }
}
