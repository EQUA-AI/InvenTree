import { t } from '@lingui/core/macro';

export interface DuplicateRepair {
  reason: string;
  work_order_id: number;
  work_order_reference: string;
  repair_packet_id: number | null;
  repair_packet_reference?: string;
  anomaly_id?: number;
  title?: string;
  status?: string;
}

export function duplicateSummary(
  duplicate: Pick<DuplicateRepair, 'reason' | 'status' | 'repair_packet_id'>
) {
  const status = duplicate.status?.replaceAll(/[_-]/g, ' ') || t`Not provided`;
  const packet = duplicate.repair_packet_id
    ? t`Repair packet`
    : t`No repair packet`;
  const reason =
    duplicate.reason === 'ANOMALY_ALREADY_LINKED'
      ? t`Source already has owning work`
      : duplicate.reason === 'OPEN_REPAIR_ON_MACHINE'
        ? t`Open repair on this machine`
        : t`Existing repair`;
  return `${status} · ${packet} · ${reason}`;
}
