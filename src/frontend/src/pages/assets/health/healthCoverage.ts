import type { MachineSignal } from '@lib/types/MachineHealth';

/** Counts facts the current signal read supports; never infers criticality. */
export function healthCoverage(signals: readonly MachineSignal[]) {
  const usable = signals.filter(
    (signal) =>
      !signal.stale &&
      signal.quality === 'good' &&
      signal.value !== null &&
      signal.value !== undefined &&
      !!signal.observed_at &&
      Number.isFinite(Date.parse(signal.observed_at))
  );
  const evaluated = usable.filter((signal) =>
    ['normal', 'warning', 'critical'].includes(signal.state)
  );
  return {
    mapped: signals.length,
    usable: usable.length,
    evaluated: evaluated.length,
    stale: signals.filter((signal) => signal.stale).length,
    // A limit named "critical_max" is not a critical-signal designation.
    criticalCoverage: null
  };
}
