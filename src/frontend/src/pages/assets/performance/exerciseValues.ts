import { unitLabel } from './format';
import type { ChartRow } from './series';
import type { PerformanceTestAlarm } from './testAlarmRows';

export function visibleExerciseSeries<
  Series extends { key: string; measuredKey?: string }
>(series: readonly Series[], hidden: ReadonlySet<string>): Series[] {
  return series.filter(
    (line) =>
      !hidden.has(line.key) &&
      (!line.measuredKey || !hidden.has(line.measuredKey))
  );
}

export function toggleExerciseSeries<
  Series extends { key: string; measuredKey?: string }
>(
  series: readonly Series[],
  hidden: ReadonlySet<string>,
  key: string
): Set<string> {
  const next = new Set(hidden);
  if (visibleExerciseSeries(series, hidden).some((line) => line.key === key))
    next.add(key);
  else {
    next.delete(key);
    const measured = series.find((line) => line.key === key)?.measuredKey;
    if (measured) next.delete(measured);
  }
  return visibleExerciseSeries(series, next).length ? next : new Set(hidden);
}

export function effectiveTest(
  alarms: readonly PerformanceTestAlarm[],
  unit?: string
) {
  return alarms
    .filter(
      (alarm) =>
        !alarm.unitMismatch &&
        (unit === undefined ||
          unitLabel(alarm.measured_unit) === unitLabel(unit))
    )
    .reduce<PerformanceTestAlarm | undefined>(
      (highest, alarm) =>
        !highest || alarm.test_value > highest.test_value ? alarm : highest,
      undefined
    );
}

export function exerciseRows(
  rows: readonly ChartRow[],
  exercises: readonly {
    key: string;
    value: number;
    since: number;
    until?: number;
  }[],
  start: number,
  end: number
): ChartRow[] {
  const result = rows.map((row) => ({ ...row }));
  const keys = new Set(
    rows.flatMap((row) => Object.keys(row).filter((key) => key !== 't'))
  );
  for (const exercise of exercises) {
    if (
      !Number.isFinite(exercise.since) ||
      exercise.since > end ||
      (exercise.until !== undefined &&
        (!Number.isFinite(exercise.until) ||
          exercise.until < exercise.since ||
          exercise.until < start)) ||
      !Number.isFinite(exercise.value)
    )
      continue;
    for (const instant of [
      Math.max(start, exercise.since),
      Math.min(end, exercise.until ?? end)
    ]) {
      if (!result.some((row) => row.t === instant))
        result.push({
          ...Object.fromEntries([...keys].map((key) => [key, null])),
          t: instant
        });
    }
  }
  return result
    .sort((first, second) => first.t - second.t)
    .map((row) => ({
      ...row,
      ...Object.fromEntries(
        exercises.map((exercise) => [
          exercise.key,
          Number.isFinite(exercise.since) &&
          Number.isFinite(exercise.value) &&
          (exercise.until === undefined ||
            (Number.isFinite(exercise.until) &&
              exercise.until >= exercise.since)) &&
          row.t >= Math.max(start, exercise.since) &&
          row.t <= Math.min(end, exercise.until ?? end)
            ? exercise.value
            : null
        ])
      )
    }));
}
