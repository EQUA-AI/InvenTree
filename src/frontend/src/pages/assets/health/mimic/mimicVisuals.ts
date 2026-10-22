import { t } from '@lingui/core/macro';
import { failedReading } from './MimicValue';
import { expandPointer, usable } from './format';
import type { Bay, MimicAlarm, MimicData, MimicPoint } from './types';

/** Source failure cannot leave a previously running symbol green. */
export function operatingState(data: MimicData, bay?: Bay): string {
  const state = bay?.state ?? 'unknown';
  if (!data.enabled) return 'unknown';
  return state !== 'unknown' && (data.read_failed || data.reporting === false)
    ? 'stale'
    : state;
}

/** Motion requires a current positive discharge reading as well as Running. */
export function hasDischargeFlow(data: MimicData, bay?: Bay): boolean {
  if (!bay || operatingState(data, bay) !== 'running' || !data.enabled)
    return false;
  const element = data.layout.elements.find(
    (item) => item.view === 'unit' && item.id === 'pump-flow'
  );
  const point = element
    ? bay.points[expandPointer(element.pointer, bay.key)]
    : undefined;
  return (
    usable(point) &&
    !failedReading(point) &&
    point.quality === 'good' &&
    typeof point.value === 'number' &&
    Number.isFinite(point.value) &&
    point.value > 0
  );
}

export function stateStyle(state: string) {
  const neutral =
    'light-dark(var(--mantine-color-gray-2), var(--mantine-color-dark-6))';
  const line =
    'light-dark(var(--mantine-color-gray-7), var(--mantine-color-dark-1))';
  switch (state) {
    case 'running':
      return {
        fill: 'light-dark(var(--mantine-color-green-1), color-mix(in srgb, var(--mantine-color-green-9) 45%, var(--mantine-color-dark-7)))',
        line: 'var(--aimms-normal, light-dark(var(--mantine-color-green-9), var(--mantine-color-green-5)))'
      };
    case 'fault':
      return {
        fill: 'light-dark(var(--mantine-color-red-1), color-mix(in srgb, var(--mantine-color-red-9) 40%, var(--mantine-color-dark-7)))',
        line: 'var(--aimms-critical, light-dark(var(--mantine-color-red-9), var(--mantine-color-red-5)))'
      };
    case 'idle':
      return { fill: neutral, line };
    case 'stale':
      return { fill: neutral, line, dashed: true };
    default:
      return { fill: 'var(--mantine-color-body)', line, dashed: true };
  }
}

export function alarmColor(alarm?: string | null) {
  return alarm === 'critical'
    ? 'var(--aimms-critical, light-dark(var(--mantine-color-red-9), var(--mantine-color-red-5)))'
    : alarm === 'warning'
      ? 'var(--aimms-warning, light-dark(var(--mantine-color-orange-9), var(--mantine-color-orange-5)))'
      : null;
}

export function alarmLabel(alarm?: string | null) {
  return alarm === 'critical'
    ? t`Critical alarm`
    : alarm === 'warning'
      ? t`Warning alarm`
      : '';
}

/** Only the source's configured verdict can colour a measured value. */
export function readingSeverity(
  point?: MimicPoint
): 'critical' | 'warning' | null {
  return usable(point) &&
    !failedReading(point) &&
    point.thresholds_configured &&
    (point.condition === 'critical' || point.condition === 'warning')
    ? point.condition
    : null;
}

/** A bay's worst severity alone says nothing about acknowledgement. */
export function alarmAttention(
  alarms: MimicAlarm[],
  machine?: number,
  pointers?: string[]
) {
  const rows = alarms.filter(
    (alarm) =>
      alarm.anomaly !== null &&
      (machine === undefined || alarm.machine === machine) &&
      (!pointers || pointers.includes(alarm.pointer))
  );
  return rows.some((alarm) => alarm.anomaly_status === 'open')
    ? 'unacknowledged'
    : rows.some((alarm) => alarm.anomaly_status === 'acknowledged')
      ? 'acknowledged'
      : undefined;
}
