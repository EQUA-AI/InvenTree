import type { MachineAnomaly } from '@lib/types/MachineHealth';

/** Recover stable plant instants using this anomaly's own serialization offset.
 * The current binding's offset can differ, and a moving display clock must not
 * become the identity of a retained occurrence's investigation window. */
export function recordedAnomalyBounds(anomaly: MachineAnomaly) {
  const shift = anomaly.display_shift_seconds;
  if (typeof shift !== 'number' || !Number.isFinite(shift)) return null;
  const recorded = (value: string) => {
    const instant = Date.parse(value) - shift * 1000;
    return Number.isFinite(instant) && Math.abs(instant) <= 8.64e15
      ? new Date(instant).toISOString()
      : null;
  };
  return {
    from: recorded(anomaly.first_observed_at),
    to: recorded(anomaly.last_observed_at)
  };
}

/**
 * Which active anomalies a signal's row and evidence may name, and how sure
 * that is. Pure: the Health table, its evidence panel and the tests share it.
 *
 * Association comes only from the anomaly's own signal list (binding ids the
 * server froze with it), never from tag or display-name matching. An anomaly
 * that names no signal is not "none for this signal" - its association is
 * unavailable, and every row has to say so rather than show a zero.
 */
export interface SignalAnomalies {
  /** Anomalies whose signal list includes this binding. */
  cited: MachineAnomaly[];
  /** Anomalies that name no signal at all: association unavailable. */
  unassociated: MachineAnomaly[];
}

export function anomaliesForSignal(
  anomalies: readonly MachineAnomaly[],
  bindingId: number
): SignalAnomalies {
  const cited: MachineAnomaly[] = [];
  const unassociated: MachineAnomaly[] = [];
  for (const anomaly of anomalies) {
    if (!['open', 'acknowledged'].includes(anomaly.status)) continue;
    const signals = anomaly.signals ?? [];
    if (signals.length === 0) {
      unassociated.push(anomaly);
    } else if (signals.some((signal) => signal.binding_id === bindingId)) {
      cited.push(anomaly);
    }
  }
  return { cited, unassociated };
}
