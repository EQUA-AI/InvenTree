import type { RoutineWork } from '@lib/types/RoutineWork';

type Reading = RoutineWork['readings'][number];

/** Group only recorded step+label associations, never guess a matched pair. */
export function groupRoutineReadings(readings: Reading[]) {
  const groups = new Map<
    string,
    { key: string; label: string; step: number | null; readings: Reading[] }
  >();
  for (const reading of readings) {
    const key =
      reading.step_execution == null
        ? `reading:${reading.id}`
        : JSON.stringify([reading.step_execution, reading.label]);
    const group = groups.get(key) ?? {
      key,
      label: reading.label,
      step: reading.step_execution,
      readings: []
    };
    group.readings.push(reading);
    groups.set(key, group);
  }
  return [...groups.values()];
}
