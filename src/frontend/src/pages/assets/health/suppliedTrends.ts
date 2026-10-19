import type { MachineSignal, SignalTrend } from '@lib/types/MachineHealth';

/** Index one machine's supplied batch without interpreting its samples. */
export function indexSuppliedTrends(
  trends: readonly SignalTrend[] | undefined
): ReadonlyMap<MachineSignal['binding_id'], SignalTrend> {
  const index = new Map<MachineSignal['binding_id'], SignalTrend>();
  for (const trend of trends ?? []) {
    index.set(trend.binding_id, trend);
  }
  return index;
}

/**
 * A missing supplied row is null, never undefined: the latter tells the
 * sparkline to start an individual request and defeats the table's batch.
 */
export function suppliedTrend(
  index: ReadonlyMap<MachineSignal['binding_id'], SignalTrend>,
  bindingId: MachineSignal['binding_id']
): SignalTrend | null {
  return index.get(bindingId) ?? null;
}
