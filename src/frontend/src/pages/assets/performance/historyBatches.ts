import type { SeriesResponse } from '@lib/types/MachineHealth';
import type { SeriesTargets } from './usePerformanceData';

/** Never turn a failed batch into an empty successful series. Preserve valid
 * sibling reads, explicitly recording partial coverage for the UI. */
export function mergeHistoryBatches(
  results: PromiseSettledResult<SeriesResponse>[],
  targets: SeriesTargets[]
): SeriesResponse {
  const responses = results.flatMap((result) =>
    result.status === 'fulfilled' ? [result.value] : []
  );
  const first = responses[0];
  if (!first) throw new Error('All bounded history reads failed');
  if (
    responses.some(
      (item) =>
        item.machine !== first.machine ||
        item.window_start !== first.window_start ||
        item.window_end !== first.window_end ||
        item.resolution_seconds !== first.resolution_seconds ||
        item.display_shift_seconds !== first.display_shift_seconds ||
        item.mode !== first.mode
    )
  ) {
    throw new Error('History batches returned incompatible windows');
  }
  return {
    ...first,
    read_batches: results.map((result, index) => ({
      target_count:
        (targets[index].bindings?.length ?? 0) +
        (targets[index].keys?.length ?? 0),
      failed: result.status === 'rejected'
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
