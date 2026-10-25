import { t } from '@lingui/core/macro';
import { failedReading } from './MimicValue';
import { expandPointer, usable } from './format';
import type { Bay, MimicAlarm, MimicData, MimicPoint, Total } from './types';

/** Source failure cannot leave a previously running symbol green. */
export function operatingState(data: MimicData, bay?: Bay): string {
  const state = bay?.state ?? 'unknown';
  if (!data.enabled) return 'unknown';
  return state !== 'unknown' && (data.read_failed || data.reporting === false)
    ? 'stale'
    : state;
}

export function stationRunningSummary(data: MimicData) {
  const states = data.bays.map((bay) => operatingState(data, bay));
  const running = states.filter((state) => state === 'running').length;
  const known = states.filter((state) =>
    ['running', 'idle', 'fault'].includes(state)
  ).length;
  const total = states.length;
  const countElement = data.layout.elements.find(
    (element) => element.id === 'pumps-running'
  );
  const counter = countElement
    ? data.station_points[countElement.pointer]
    : undefined;
  const reported =
    data.enabled &&
    data.reporting !== false &&
    !data.read_failed &&
    usable(counter) &&
    !failedReading(counter) &&
    counter.quality === 'good' &&
    typeof counter.value === 'number' &&
    Number.isFinite(counter.value)
      ? counter.value
      : null;
  return {
    running,
    total,
    known,
    unknown: total - known,
    reported,
    disagreement:
      total > 0 && known === total && reported !== null && reported !== running
  };
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

/** Absence is known only with complete mapping and an explicitly quiet station. */
export function knownNoActiveAlarms(data: MimicData | undefined): boolean {
  return (
    !!data &&
    data.enabled &&
    data.source != null &&
    data.reporting !== false &&
    !data.read_failed &&
    !data.last_error_code &&
    data.alarm === null &&
    data.bays.length > 0 &&
    data.bays.every((bay) => bay.machine != null && bay.alarm === null) &&
    !data.alarms.some(
      (alarm) =>
        alarm.anomaly != null &&
        ['open', 'acknowledged'].includes(alarm.anomaly_status ?? '')
    )
  );
}

/** Missing contributors explain an unavailable sum without treating them as zero. */
export function missingContributors(total: Total, bays: Bay[]): string | null {
  if (!total.derived || total.value !== null) return null;
  const missing = total.contributors.filter(
    (pointer) =>
      !bays.some(
        (bay) =>
          usable(bay.points[pointer]) && !failedReading(bay.points[pointer])
      )
  );
  return missing.length
    ? t`${missing.length} of ${bays.length} bays not reporting`
    : null;
}
