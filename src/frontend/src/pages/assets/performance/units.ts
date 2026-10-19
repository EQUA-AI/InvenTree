/**
 * R6 A19.7 / A19.15: which readings may share one scale.
 *
 * Two readings share an axis, a family card, a difference or a sum only when
 * their units say they are the same quantity at the same magnitude. Units
 * come from the reviewed dictionary and are compared as written: "degC" and
 * "°C" are kept apart rather than assumed equal, an empty unit never joins a
 * stated one, and nothing is rescaled - a kW reading is not divided into MW.
 * The one deliberate equivalence is S3-03's: active, reactive and apparent
 * power (W, var, VA) at the same SI prefix share a power axis.
 */

const POWER_UNIT = /^([kMG]?)(W|var|VA)$/i;
const PREFIXES = new Set(['', 'k', 'M', 'G']);

/** The scale a unit belongs to; equal keys may share an axis. */
export function scaleKey(unit: string | null | undefined): string {
  const text = (unit ?? '').trim();
  const power = POWER_UNIT.exec(text);
  if (power) {
    // "MVar" and "Mvar" are one unit; "mW" (milli) is not "MW".
    const prefix = power[1];
    if (PREFIXES.has(prefix)) return `power:${prefix}`;
  }
  return `unit:${text}`;
}

export function sameScale(
  a: string | null | undefined,
  b: string | null | undefined
): boolean {
  return scaleKey(a) === scaleKey(b);
}

export interface UnitGroup<T> {
  key: string;
  /** The units as written in this group, in first-seen order. */
  units: string[];
  items: T[];
}

/** Items grouped by scale, in the order their first member appears. */
export function groupByScale<T>(
  items: readonly T[],
  unitOf: (item: T) => string | null | undefined
): UnitGroup<T>[] {
  const groups = new Map<string, UnitGroup<T>>();
  for (const item of items) {
    const unit = (unitOf(item) ?? '').trim();
    const key = scaleKey(unit);
    let group = groups.get(key);
    if (!group) {
      group = { key, units: [], items: [] };
      groups.set(key, group);
    }
    if (!group.units.includes(unit)) group.units.push(unit);
    group.items.push(item);
  }
  return [...groups.values()];
}

/** The group with the most members; the earliest wins a tie. */
export function largestGroup<T>(
  groups: readonly UnitGroup<T>[]
): UnitGroup<T> | undefined {
  return groups.reduce<UnitGroup<T> | undefined>(
    (best, group) =>
      best === undefined || group.items.length > best.items.length
        ? group
        : best,
    undefined
  );
}

/** "MW / MVar", or the empty string for a dimensionless group. */
export function unitText(group: Pick<UnitGroup<unknown>, 'units'>): string {
  return group.units.filter(Boolean).join(' / ');
}

export interface AxisLine {
  unit: string;
  yAxisId?: 'left' | 'right';
}

export interface AxisSplit<L extends AxisLine> {
  /** The lines that stay on the chart: one scale per axis. */
  main: L[];
  /** Whether each axis kept every line it was given. */
  leftWhole: boolean;
  rightWhole: boolean;
  leftUnits: string;
  rightUnits: string;
  /** Lines whose scale differs from their axis's first scale, by scale. */
  apart: { axis: 'left' | 'right'; group: UnitGroup<L> }[];
}

/**
 * Keep the first scale on each axis and set every other scale apart. Each
 * axis is judged on its own: a left axis in rpm beside steps on the right
 * is two scales, which is what a second axis is for.
 */
export function splitAxes<L extends AxisLine>(
  lines: readonly L[]
): AxisSplit<L> {
  const left = groupByScale(
    lines.filter((line) => line.yAxisId !== 'right'),
    (line) => line.unit
  );
  const right = groupByScale(
    lines.filter((line) => line.yAxisId === 'right'),
    (line) => line.unit
  );
  const kept = new Set<L>([
    ...(left[0]?.items ?? []),
    ...(right[0]?.items ?? [])
  ]);
  return {
    main: lines.filter((line) => kept.has(line)),
    leftWhole: left.length <= 1,
    rightWhole: right.length <= 1,
    leftUnits: left[0] ? unitText(left[0]) : '',
    rightUnits: right[0] ? unitText(right[0]) : '',
    apart: [
      ...left.slice(1).map((group) => ({ axis: 'left' as const, group })),
      ...right.slice(1).map((group) => ({ axis: 'right' as const, group }))
    ]
  };
}
