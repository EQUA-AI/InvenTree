import type { MachineSignal } from '@lib/types/MachineHealth';

import {
  PARAMETERS,
  type ParameterCategory,
  type ParameterDefinition
} from './parameters';

/**
 * A signal the machine actually has, matched to what it means.
 *
 * `signal` is the authority on value, unit, quality, freshness and limits.
 * `definition` says which family it belongs to and how to draw it. `index` and
 * `side` identify the sensor within its family - winding 7, the left outlet.
 */
export interface ResolvedParameter {
  definition: ParameterDefinition;
  signal: MachineSignal;
  index: number | null;
  side: string | null;
  /** The label a chart legend or a KPI tile shows. */
  label: string;
  /** A stable key for React and for chart series names. */
  id: string;
}

const PUMP_PREFIX = /^\/dex\/PUMP\d+_/i;
const PUMP_SUMMARY = /^\/pd\/P\d+\//i;

/**
 * Strip the station-specific prefix off a mapped key.
 *
 * `/dex/PUMP12_ACTIVE_POWER` becomes `ACTIVE_POWER`; a pump summary field such
 * as `/pd/P3/st` becomes `pd:st`; a station-level key keeps its own name.
 */
export function tagOf(externalKey: string): string {
  if (PUMP_PREFIX.test(externalKey)) {
    return externalKey.replace(PUMP_PREFIX, '');
  }
  if (PUMP_SUMMARY.test(externalKey)) {
    return `pd:${externalKey.replace(PUMP_SUMMARY, '')}`;
  }
  return externalKey.replace(/^\/dex\//i, '').replace(/^\//, '');
}

const OTHER: ParameterDefinition = {
  key: 'OTHER',
  label: () => '',
  category: 'other',
  role: 'other',
  match: /$^/,
  decimals: 2,
  chart: 'line'
};

function titleCase(side: string): string {
  return side
    .toLowerCase()
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/** Match one signal against the catalogue. */
export function resolveSignal(signal: MachineSignal): ResolvedParameter {
  const tag = tagOf(signal.external_key);

  for (const definition of PARAMETERS) {
    const found = definition.match.exec(tag);
    if (!found) {
      continue;
    }
    const index = found[1] != null ? Number.parseInt(found[1], 10) : null;
    const side = found.groups?.side ? titleCase(found.groups.side) : null;
    const parts = [definition.label()];
    if (side) {
      parts.push(side);
    }
    if (index !== null && !Number.isNaN(index)) {
      parts.push(String(index));
    }
    return {
      definition,
      signal,
      index: index !== null && !Number.isNaN(index) ? index : null,
      side,
      label: parts.join(' '),
      id: `${definition.key}:${signal.binding_id}`
    };
  }

  return {
    definition: OTHER,
    signal,
    index: null,
    side: null,
    label: signal.display_name || tag,
    id: `OTHER:${signal.binding_id}`
  };
}

/**
 * Resolve every signal, in a stable order: by category, then role, then side,
 * then sensor index, then label. Families therefore come out contiguous and
 * sensors numbered, whatever order the API listed them in.
 */
export function resolveParameters(
  signals: readonly MachineSignal[]
): ResolvedParameter[] {
  const resolved = signals.map(resolveSignal);
  const categoryRank = new Map<ParameterCategory, number>();
  for (const [rank, category] of [
    'electrical',
    'operation',
    'hydraulic',
    'winding',
    'vibration',
    'cooling',
    'bearing',
    'station',
    'other'
  ].entries()) {
    categoryRank.set(category as ParameterCategory, rank);
  }
  resolved.sort((a, b) => {
    const category =
      (categoryRank.get(a.definition.category) ?? 99) -
      (categoryRank.get(b.definition.category) ?? 99);
    if (category !== 0) return category;
    if (a.definition.role !== b.definition.role) {
      return a.definition.role.localeCompare(b.definition.role);
    }
    if ((a.side ?? '') !== (b.side ?? '')) {
      return (a.side ?? '').localeCompare(b.side ?? '');
    }
    if (a.index !== b.index) {
      return (a.index ?? 0) - (b.index ?? 0);
    }
    return a.label.localeCompare(b.label);
  });
  return resolved;
}

export function inCategory(
  parameters: readonly ResolvedParameter[],
  category: ParameterCategory
): ResolvedParameter[] {
  return parameters.filter((p) => p.definition.category === category);
}

export function withRole(
  parameters: readonly ResolvedParameter[],
  ...roles: string[]
): ResolvedParameter[] {
  return parameters.filter((p) => roles.includes(p.definition.role));
}

export function withKey(
  parameters: readonly ResolvedParameter[],
  key: string
): ResolvedParameter | undefined {
  return parameters.find((p) => p.definition.key === key);
}

/**
 * Whether a parameter can be drawn on a numeric axis at all. A status trace can
 * - the server maps it to a number - so only bindings without an id are out.
 */
export function chartable(parameter: ResolvedParameter): boolean {
  return parameter.signal.binding_id != null;
}
