import type { MachineSignal } from '@lib/types/MachineHealth';

/** Counts facts the current signal read supports; never infers criticality. */
export function healthCoverage(signals: readonly MachineSignal[]) {
  const usable = signals.filter(
    (signal) =>
      signal.active !== false &&
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
  const designated = signals.filter(
    (signal) => signal.critical_designation?.critical === true
  );
  return {
    mapped: signals.filter((signal) => signal.active !== false).length,
    usable: usable.length,
    evaluated: evaluated.length,
    stale: signals.filter((signal) => signal.stale).length,
    // A limit named "critical_max" is not a critical-signal designation.
    criticalCoverage: designated.length
      ? {
          designated: designated.length,
          usable: usable.filter(
            (signal) =>
              signal.critical_designation?.critical === true &&
              signal.critical_designation?.review_required !== true &&
              signal.critical_designation?.applies_current !== false
          ).length,
          evaluated: evaluated.filter(
            (signal) =>
              signal.critical_designation?.critical === true &&
              signal.critical_designation?.review_required !== true &&
              signal.critical_designation?.applies_current !== false
          ).length
        }
      : null
  };
}
