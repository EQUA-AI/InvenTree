import type { LocatedMachine } from './locationTypes';
import { machineCondition } from './machineCondition';

/** Facts about exactly the loaded page, never extrapolated to a station. */
export function pageFacts(rows: LocatedMachine[]) {
  let alerts = 0;
  let offline = 0;
  let unknown = 0;
  let stale = 0;
  let alarmRows: number | null = 0;
  for (const row of rows) {
    const condition = machineCondition(row.condition);
    if (condition.kind === 'unavailable' || condition.state === 'unknown')
      unknown++;
    else if (condition.state === 'offline') offline++;
    else if (['warning', 'critical'].includes(condition.state)) alerts++;
    if (
      condition.kind === 'assessed' &&
      row.condition &&
      row.condition.stale_signal_count > 0
    )
      stale++;
    // Station row counts already include child alarms: count rows, never sum events.
    const count = row.open_alarms;
    if (
      alarmRows !== null &&
      typeof count === 'number' &&
      Number.isSafeInteger(count) &&
      count >= 0
    )
      alarmRows += count > 0 ? 1 : 0;
    else alarmRows = null;
  }
  return { alerts, offline, unknown, stale, alarmRows };
}
