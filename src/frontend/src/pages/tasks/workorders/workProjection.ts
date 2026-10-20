import { displayWork } from '@lib/types/CanonicalDisplay';
import type { CloseoutWorkOrder } from '@lib/types/Closeout';
import type { BoardCard } from '@lib/types/Tasks';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';

export function boardCardIdentity(card: Pick<BoardCard, 'id' | 'work_order'>) {
  return { id: card.id, workOrderId: card.work_order };
}

export function projectCloseoutWorkOrder(
  workOrder: Pick<
    WorkOrderOverview,
    'id' | 'reference' | 'title' | 'lifecycle_status' | 'lifecycle_version'
  >
): CloseoutWorkOrder {
  return displayWork({
    id: workOrder.id,
    reference: workOrder.reference ?? `WO-${workOrder.id}`,
    title: workOrder.title,
    lifecycle_status: workOrder.lifecycle_status,
    lifecycle_version: workOrder.lifecycle_version
  }).record;
}
