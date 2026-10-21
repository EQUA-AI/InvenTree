import type {
  MachineAnomaly,
  MachineHealthSummary,
  MachineSignal
} from '@lib/types/MachineHealth';
import { healthCoverage } from './healthCoverage';

/** Qualifies the supplied state; it never computes a replacement verdict. */
export function headlineFacts(
  summary: MachineHealthSummary,
  signals: readonly MachineSignal[] | null,
  anomalies: readonly MachineAnomaly[] | null = null
) {
  const coverage = signals ? healthCoverage(signals) : null;
  const critical = coverage?.criticalCoverage;
  const coverageIncomplete =
    !coverage ||
    coverage.evaluated < coverage.mapped ||
    !critical ||
    critical.evaluated < critical.designated;
  const criticalMissing = (signals ?? [])
    .filter(
      (signal) =>
        signal.critical_designation?.critical &&
        (signal.critical_designation.review_required ||
          signal.critical_designation.applies_current === false ||
          signal.active === false ||
          signal.stale ||
          signal.value == null ||
          !signal.observed_at ||
          signal.quality !== 'good' ||
          !['normal', 'warning', 'critical'].includes(signal.state))
    )
    .map((signal) => ({
      signal,
      reason:
        signal.critical_designation?.review_required ||
        signal.critical_designation?.applies_current === false ||
        signal.active === false
          ? 'review required'
          : signal.stale
            ? 'not current'
            : signal.value == null || !signal.observed_at
              ? 'no observation'
              : signal.quality !== 'good'
                ? 'quality not good'
                : 'not evaluated'
    }));
  const matchingSignals =
    summary.state === 'warning' || summary.state === 'critical'
      ? (signals ?? []).filter((signal) => signal.state === summary.state)
      : [];
  const why =
    summary.state !== 'warning' && summary.state !== 'critical'
      ? 'not-needed'
      : matchingSignals.length
        ? 'signals'
        : anomalies?.length
          ? 'anomaly'
          : 'unavailable';
  return {
    coverage,
    critical,
    coverageIncomplete,
    criticalMissing,
    matchingSignals,
    why,
    anomaly: anomalies?.[0],
    noObservation:
      !!coverage &&
      coverage.mapped > 0 &&
      coverage.usable === 0 &&
      (signals ?? [])
        .filter((s) => s.active !== false)
        .every((s) => s.value == null)
  };
}
