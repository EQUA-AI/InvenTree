import { t } from '@lingui/core/macro';

import type {
  Bay,
  LayoutElement,
  MimicData,
  MimicLayout,
  MimicPoint
} from './types';

/** A span in the largest unit that still reads honestly, for the mimic. */
export function unchangedLabel(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  if (days >= 1) {
    return days === 1 ? t`1 day` : t`${days} days`;
  }
  const hours = Math.floor(seconds / 3600);
  if (hours >= 1) {
    return hours === 1 ? t`1 hour` : t`${hours} hours`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes >= 1) {
    return minutes === 1 ? t`1 minute` : t`${minutes} minutes`;
  }
  return t`${Math.round(seconds)} seconds`;
}

export function reasonLabel(reason: string | null): string {
  const labels: Record<string, string> = {
    disabled: t`Polling disabled`,
    not_bound: t`Not bound`,
    not_approved: t`Not approved`,
    no_data: t`No reading`,
    stale: t`Stale`,
    bad_quality: t`Unusable quality`,
    over_range: t`Sensor over range`,
    railed: t`Sensor at full scale`,
    not_finite: t`Not a finite number`,
    clock_skew: t`Source clock is ahead`,
    mapping_changed: t`Mapping changed`,
    type_mismatch: t`Unexpected value type`,
    inactive_equipment: t`Inactive equipment`,
    incomplete: t`Missing contributors`,
    unconfirmed_unit: t`Unit needs review`,
    incompatible_unit: t`Incompatible units`
  };
  return reason ? (labels[reason] ?? t`Unavailable`) : '';
}

export function stateLabel(state: string) {
  const labels: Record<string, string> = {
    running: t`Running`,
    idle: t`Idle`,
    fault: t`Fault`,
    stale: t`Stale`,
    not_bound: t`Not bound`,
    unknown: t`Unknown`
  };
  return labels[state] ?? t`Unknown`;
}

/** Whether a point has a reading that may be shown as current. */
export function usable(point?: MimicPoint): point is MimicPoint {
  return !!point && point.value !== null && !point.reason;
}

export function numberText(value: number, digits = 3) {
  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function valueText(point?: MimicPoint) {
  if (!usable(point)) return t`Unavailable`;
  if (typeof point.value === 'boolean') return point.value ? t`Yes` : t`No`;
  const value =
    typeof point.value === 'number' ? numberText(point.value) : point.value;
  return `${value}${point.unit ? ` ${point.unit}` : ''}`;
}

/**
 * A status reading in words.
 *
 * The source sends a code - `R`, `I` - and the layout says which state each
 * one means. A code the layout does not know is shown as it came: nothing here
 * guesses at a meaning the plant has not confirmed.
 */
export function statusText(point: MimicPoint | undefined, layout: MimicLayout) {
  if (!usable(point)) return t`Unavailable`;
  const code = String(point.value);
  const state = Object.entries(layout.status_values ?? {}).find(([, codes]) =>
    codes.includes(code)
  );
  return state ? stateLabel(state[0]) : code;
}

export function elementLabel(element: LayoutElement) {
  const labels: Record<string, string> = {
    forebay: t`Forebay level`,
    'station-status': t`Station status`,
    'pumps-running': t`Pumps running`,
    'pump-status': t`Pump status`,
    'pump-power': t`Input power`,
    'pump-speed': t`Shaft speed`,
    'pump-pressure': t`Discharge pressure`,
    'pump-flow': t`Discharge flow`
  };
  return labels[element.id] ?? element.label;
}

/**
 * How a reading's value is written for this station and bay.
 *
 * A status reading is a code. The layout says which readings are statuses and
 * what each code means, and those are written in words wherever they appear -
 * on the drawing, in a summary, in a table - so the three never disagree.
 */
export function displayFor(data: MimicData | undefined, unit: string | null) {
  const statuses = new Set<string>();
  for (const element of data?.layout.elements ?? []) {
    if (element.role !== 'status') continue;
    if (element.view === 'station') statuses.add(element.pointer);
    else if (unit) statuses.add(expandPointer(element.pointer, unit));
  }
  return (point: MimicPoint) =>
    data && statuses.has(point.pointer)
      ? statusText(point, data.layout)
      : valueText(point);
}

/** Resolve a layout pointer for one bay, as the server does. */
export function expandPointer(template: string, unit: string) {
  return template
    .replaceAll('{pump_number}', unit.replace(/^P/, ''))
    .replaceAll('{pump}', unit.replaceAll('~', '~0').replaceAll('/', '~1'));
}

/**
 * Bays in the order they are numbered.
 *
 * The server sorts their keys as text, which puts P10 to P14 between P1 and
 * P2 - correct for a key, wrong for a row of pumps someone has to find one in.
 */
export function byBayNumber(a: Bay, b: Bay) {
  const number = (bay: Bay) => Number(/\d+/.exec(bay.key)?.[0] ?? Number.NaN);
  const [x, y] = [number(a), number(b)];
  if (Number.isNaN(x) || Number.isNaN(y) || x === y) {
    return a.key.localeCompare(b.key);
  }
  return x - y;
}
