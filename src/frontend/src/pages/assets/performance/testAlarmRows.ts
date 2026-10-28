import type { MachineSignal, SignalTestAlarm } from '@lib/types/MachineHealth';
import { unitLabel } from './format';

export interface PerformanceTestAlarm extends SignalTestAlarm {
  binding_id: number;
  label: string;
  measured_value: MachineSignal['value'];
  measured_unit: string;
  measured_stale: boolean;
  measured_quality: MachineSignal['quality'];
  unitMismatch: boolean;
}

export function testAlarmRows(
  signals: readonly MachineSignal[],
  labels?: ReadonlyMap<number, string>,
  includeResolved = false
): PerformanceTestAlarm[] {
  return signals.flatMap((signal) =>
    (signal.test_alarms ?? [])
      .filter(
        (alarm) =>
          (['open', 'acknowledged'].includes(alarm.status) ||
            (includeResolved &&
              alarm.status === 'resolved' &&
              Number.isFinite(Date.parse(alarm.triggered_at)) &&
              !!alarm.resolved_at &&
              Number.isFinite(Date.parse(alarm.resolved_at)) &&
              Date.parse(alarm.resolved_at) >=
                Date.parse(alarm.triggered_at))) &&
          Number.isFinite(alarm.test_value)
      )
      .map((alarm) => ({
        ...alarm,
        binding_id: signal.binding_id,
        label:
          !labels?.has(signal.binding_id) ||
          labels.get(signal.binding_id) === signal.display_name
            ? signal.display_name
            : `${labels.get(signal.binding_id)} (${signal.display_name})`,
        measured_value: signal.value,
        measured_unit: signal.unit,
        measured_stale: signal.stale,
        measured_quality: signal.quality,
        unitMismatch:
          unitLabel(alarm.unit).trim() !== unitLabel(signal.unit).trim()
      }))
  );
}
