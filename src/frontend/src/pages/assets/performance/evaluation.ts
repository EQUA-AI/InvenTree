import type { MachineSignal } from '@lib/types/MachineHealth';

/**
 * R6 UI05 "Anything unusual?": what the current readings can actually say.
 *
 * A signal is *evaluated* only when the server classified it against its own
 * configured limits - normal, warning or critical. A signal with no limits,
 * or whose reading is stale or of unusable quality, has no verdict, so a calm
 * summary is never inferred from signals nobody judged. This is the verdict
 * on the current readings only; it says nothing about the history window.
 */

type Signal = Pick<MachineSignal, 'state' | 'stale' | 'quality' | 'limits'>;

export type PerformanceVerdict =
  /** No signal has a limit: there is nothing to be outside of. */
  | { kind: 'no-limits' }
  /** Limits exist, but no reading could be judged against them now. */
  | { kind: 'unavailable' }
  /** At least one reading is outside its limits; the badges name them. */
  | { kind: 'outside' }
  /** Some readings could not be judged, so calm is not claimed. */
  | { kind: 'limited' }
  /**
   * Every signal with limits was judged and is within them, and no reading
   * was stale or unusable.
   */
  | { kind: 'calm' };

export interface PerformanceEvaluation {
  verdict: PerformanceVerdict;
  /** Signals the server classified against configured limits. */
  evaluated: number;
  /** Signals with any configured limit. */
  configured: number;
  total: number;
}

export function hasLimits(signal: Pick<MachineSignal, 'limits'>): boolean {
  const l = signal.limits;
  return (
    l.warn_min !== null ||
    l.warn_max !== null ||
    l.critical_min !== null ||
    l.critical_max !== null ||
    l.normal_min !== null ||
    l.normal_max !== null
  );
}

export function evaluatePerformance(
  signals: readonly Signal[]
): PerformanceEvaluation {
  const configured = signals.filter(hasLimits).length;
  const judged = signals.filter((s) =>
    ['normal', 'warning', 'critical'].includes(s.state)
  );
  const outside = judged.some((s) => s.state !== 'normal');
  const unusable = signals.some((s) => s.stale || s.quality !== 'good');
  // A limited signal can still go unjudged on a fresh, good reading - a text
  // value such as "ON" has no number to hold against its limits.
  const unjudged = judged.length < configured;
  const verdict: PerformanceVerdict =
    configured === 0
      ? { kind: 'no-limits' }
      : outside
        ? { kind: 'outside' }
        : judged.length === 0
          ? { kind: 'unavailable' }
          : unusable || unjudged
            ? { kind: 'limited' }
            : { kind: 'calm' };
  return {
    verdict,
    evaluated: judged.length,
    configured,
    total: signals.length
  };
}

/**
 * R6 UI05 electrical axes: reactive power may share the active-power axis
 * only when both units are stated with the same magnitude (W/var, kW/kvar,
 * MW/Mvar). Anything else - an unknown, missing or mismatched unit - is drawn
 * on its own chart rather than rescaled or guessed.
 */
const MAGNITUDES = new Set(['', 'k', 'M', 'G']);

function magnitude(unit: string | null | undefined, base: RegExp) {
  const match = (unit ?? '').trim().match(base);
  return match && MAGNITUDES.has(match[1]) ? match[1] : null;
}

export function reactiveSharesPowerAxis(
  powerUnits: readonly (string | null | undefined)[],
  reactiveUnits: readonly (string | null | undefined)[]
): boolean {
  if (!powerUnits.length || !reactiveUnits.length) return false;
  const power = powerUnits.map((unit) => magnitude(unit, /^([kMG]?)W$/));
  const reactive = reactiveUnits.map((unit) =>
    magnitude(unit, /^([kMG]?)var$/i)
  );
  const first = power[0];
  return (
    first !== null &&
    [...power, ...reactive].every((candidate) => candidate === first)
  );
}
