import type { SeriesResponse } from '@lib/types/MachineHealth';
import type { SeriesTargets } from './usePerformanceData';

/** Per-request clocks can cross a whole-second boundary. Larger differences
 * can represent different plant periods, even with minute-long sample slots. */
const CLOCK_SKEW_SECONDS = 1;

export function failedHistoryTargets(response: SeriesResponse | undefined) {
  const failed = response?.read_batches?.filter((batch) => batch.failed) ?? [];
  return {
    bindings: new Set(failed.flatMap((batch) => [...batch.bindings])),
    keys: new Set(failed.flatMap((batch) => [...batch.keys]))
  };
}

/** Never turn a failed or incoherent batch into successful empty history. */
export function mergeHistoryBatches(
  results: PromiseSettledResult<SeriesResponse>[],
  targets: SeriesTargets[]
): SeriesResponse {
  const first = results.find((result) => result.status === 'fulfilled')?.value;
  if (!first) throw new Error('All bounded history reads failed');
  const accepted = results.map((result) => {
    if (result.status !== 'fulfilled') return undefined;
    const item = result.value;
    return item.machine === first.machine &&
      item.history_source === first.history_source &&
      item.source_id === first.source_id &&
      item.telemetry_presentation === first.telemetry_presentation &&
      item.window_start === first.window_start &&
      item.window_end === first.window_end &&
      item.mode === first.mode &&
      item.slots === first.slots &&
      Number.isFinite(item.display_shift_seconds) &&
      Math.abs(item.display_shift_seconds - first.display_shift_seconds) <=
        CLOCK_SKEW_SECONDS
      ? item
      : undefined;
  });
  const responses = accepted.filter((item): item is SeriesResponse => !!item);
  if (!responses.length) throw new Error('All bounded history reads failed');
  return {
    ...first,
    // Conservative gap threshold for aggregate charts. Actual measured values
    // stay attached to each batch, rather than claiming identical cadences.
    resolution_seconds: Math.min(
      ...responses.map((item) => item.resolution_seconds)
    ),
    read_batches: results.map((_, index) => ({
      target_count:
        (targets[index].bindings?.length ?? 0) +
        (targets[index].keys?.length ?? 0),
      bindings: targets[index].bindings ?? [],
      keys: targets[index].keys ?? [],
      failed: !accepted[index],
      ...(accepted[index]
        ? {
            resolution_seconds: accepted[index].resolution_seconds,
            cadence_seconds: accepted[index].cadence_seconds,
            display_shift_seconds: accepted[index].display_shift_seconds
          }
        : {})
    })),
    series: [
      ...new Map(
        responses
          .flatMap((item) => item.series)
          .map((entry) => [entry.binding_id, entry])
      ).values()
    ]
  };
}
