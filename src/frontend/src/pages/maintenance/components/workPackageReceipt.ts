import type { RetainedCommand } from '../../../hooks/useRetainedCommand';

export function matchesWorkPackageReceipt(
  data: unknown,
  request: RetainedCommand
): boolean {
  if (!data || typeof data !== 'object') return false;
  const receipt = data as Record<string, unknown>;
  const source = request.body.source as Record<string, unknown> | undefined;
  const planning = request.body.planning as Record<string, unknown> | undefined;
  return (
    Number.isSafeInteger(receipt.work_order_id) &&
    Number(receipt.work_order_id) > 0 &&
    receipt.machine_id === request.body.machine_id &&
    receipt.idempotency_key === request.body.idempotency_key &&
    (receipt.anomaly_id ?? null) === (source?.anomaly_id ?? null) &&
    (receipt.assigned_to_id ?? null) === (planning?.assigned_to_id ?? null) &&
    (request.body.create_repair_packet !== true ||
      (Number.isSafeInteger(receipt.repair_packet_id) &&
        Number(receipt.repair_packet_id) > 0))
  );
}
