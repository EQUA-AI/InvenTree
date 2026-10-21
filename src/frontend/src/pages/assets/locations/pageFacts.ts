import type { LocatedMachine } from './locationTypes';
import { machineCondition } from './machineCondition';

/** Mutually exclusive assessments of exactly this loaded page; stale overlaps. */
export function pageFacts(rows: LocatedMachine[]) {
  const facts = {
    critical: 0,
    warning: 0,
    offline: 0,
    unknown: 0,
    unavailable: 0,
    normal: 0,
    stale: 0,
    alarmRows: 0 as number | null
  };
  for (const row of rows) {
    const condition = machineCondition(row.condition);
    if (condition.kind === 'unavailable') facts.unavailable++;
    else facts[condition.state]++;
    if (
      condition.kind === 'assessed' &&
      row.condition &&
      row.condition.stale_signal_count > 0
    )
      facts.stale++;
    const count = row.open_alarms;
    if (
      facts.alarmRows !== null &&
      typeof count === 'number' &&
      Number.isSafeInteger(count) &&
      count >= 0
    )
      facts.alarmRows += count > 0 ? 1 : 0;
    else facts.alarmRows = null;
  }
  return facts;
}
