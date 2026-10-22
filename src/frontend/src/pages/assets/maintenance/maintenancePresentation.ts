import type { AssetMaintenanceRecord } from '@lib/types/Assets';
import { t } from '@lingui/core/macro';

/** Absence is not the same as a work link withheld by authorization. */
export function maintenanceMissingReasons(record: AssetMaintenanceRecord) {
  const reasons: Record<string, string> = {};
  const absent = (value: unknown) =>
    value == null || (typeof value === 'string' && !value.trim());
  if (absent(record.summary)) reasons.summary = t`Summary not recorded`;
  if (!record.work_order)
    reasons.work_order =
      record.work_order_link_state === 'none'
        ? t`No linked work order`
        : t`Work order link not available`;
  if (absent(record.work_order_title))
    reasons.work_order_title = t`Job not recorded`;
  if (absent(record.work_order_component))
    reasons.work_order_component = t`Component not recorded`;
  if (absent(record.work_order_type))
    reasons.work_order_type = t`Work type not recorded`;
  if (absent(record.performed_by))
    reasons.performed_by = t`Technician not recorded`;
  if (absent(record.lifecycle_status))
    reasons.lifecycle_status = t`Outcome not recorded`;
  if (record.downtime_minutes == null)
    reasons.downtime_minutes = t`Downtime not recorded`;
  if (record.follow_up_required == null)
    reasons.follow_up_required = t`Follow-up status not recorded`;
  if (absent(record.details)) reasons.details = t`Details not recorded`;
  return reasons;
}

/** Collapse a column only when the whole loaded page has the same absence. */
export function emptyMaintenanceColumns(records: AssetMaintenanceRecord[]) {
  if (!records.length) return {};
  const reasons = records.map(maintenanceMissingReasons);
  return Object.fromEntries(
    Object.entries(reasons[0]).filter(([column, reason]) =>
      reasons.every((row) => row[column] === reason)
    )
  );
}
