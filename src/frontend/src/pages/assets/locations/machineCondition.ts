import type { HealthState } from '@lib/types/MachineHealth';

/**
 * R6 UI01: a machine's Health condition as the Machines list shows it.
 *
 * The server judges each row exactly as the machine's Health tab does and
 * sends the state with its coverage. A limited assessment carries one
 * coverage note; an absent or malformed value is "not available" — never a
 * normal condition.
 */

/** The row field: the parts of the Health summary the list shows. */
export interface MachineConditionWire {
  state: HealthState;
  configured: boolean;
  signal_count: number;
  stale_signal_count: number;
  degraded_data: boolean;
}

export type MachineCoverage =
  | { kind: 'full' }
  | { kind: 'unmapped' }
  | { kind: 'no-current-data' }
  | { kind: 'stale'; stale: number; signals: number }
  | { kind: 'poor-quality' };

export type MachineConditionSummary =
  | { kind: 'unavailable' }
  | { kind: 'assessed'; state: HealthState; coverage: MachineCoverage };

const STATES: readonly HealthState[] = [
  'normal',
  'warning',
  'critical',
  'offline',
  'unknown'
];

const count = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

export function machineCondition(value: unknown): MachineConditionSummary {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    return { kind: 'unavailable' };
  const row = value as Record<string, unknown>;
  const state = STATES.find((candidate) => candidate === row.state);
  const signals = row.signal_count;
  const stale = row.stale_signal_count;
  if (
    !state ||
    typeof row.configured !== 'boolean' ||
    typeof row.degraded_data !== 'boolean' ||
    !count(signals) ||
    !count(stale) ||
    stale > signals
  )
    return { kind: 'unavailable' };
  const coverage: MachineCoverage = !row.configured
    ? { kind: 'unmapped' }
    : state === 'offline'
      ? { kind: 'no-current-data' }
      : stale > 0
        ? { kind: 'stale', stale, signals }
        : row.degraded_data
          ? { kind: 'poor-quality' }
          : { kind: 'full' };
  return { kind: 'assessed', state, coverage };
}
