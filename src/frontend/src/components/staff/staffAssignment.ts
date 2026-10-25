import type {
  AssignmentCommand,
  StaffMember,
  StaffScope
} from '@lib/types/Staff';
import type { BoardCard, WorkOrder } from '@lib/types/Tasks';
import type { UserProps } from '@lib/types/User';

export function staffCapabilities(
  user?: Pick<UserProps, 'is_superuser' | 'permissions'>
) {
  const allowed = (model: string, action: string) =>
    !!user?.is_superuser || !!user?.permissions?.[model]?.includes(action);
  return {
    directory: allowed('staff_directory', 'view'),
    assign: allowed('workorder', 'assign'),
    plan: allowed('workorder', 'plan'),
    override: allowed('assignment_trade_mismatch', 'override')
  };
}

export function staffScopeParams(scope: StaffScope) {
  return scope.workOrderId !== undefined
    ? {
        work_order_id: scope.workOrderId,
        ...(scope.kanbanCardId != null
          ? { kanban_card_id: scope.kanbanCardId }
          : {})
      }
    : { machine_id: scope.machineId };
}

export type AssignmentReview = 'unassigned' | 'unknown' | 'match' | 'mismatch';

/** A UI warning only; the command revalidates all permissions and qualifications. */
export function assignmentReview(
  staff: StaffMember | undefined,
  requiredTrades: string[],
  confirmed: boolean
): AssignmentReview {
  if (!staff) return 'unassigned';
  if (!confirmed || requiredTrades.length === 0) return 'unknown';
  const approved = new Set(
    staff.trades.filter((trade) => trade.active).map((trade) => trade.code)
  );
  return requiredTrades.every((code) => approved.has(code))
    ? 'match'
    : 'mismatch';
}

export function resolveCardAssignment(
  card: Pick<
    BoardCard,
    'assigned_to' | 'assignee' | 'effective_assignee' | 'assigned_to_name'
  >,
  workOrder?: Pick<WorkOrder, 'assigned_to' | 'assigned_to_name' | 'assignee'>
) {
  const userId =
    card.assigned_to ??
    card.effective_assignee ??
    workOrder?.assigned_to ??
    null;
  const name =
    card.assigned_to != null
      ? card.assigned_to_name || ''
      : workOrder?.assigned_to_name || '';
  return {
    userId,
    // Never turn a name into an identity, or show an inherited name for a card override.
    label: userId != null ? name : card.assignee || workOrder?.assignee || '',
    inherited: card.assigned_to == null
  };
}

export function newAssignmentKey() {
  return typeof crypto?.randomUUID === 'function'
    ? crypto.randomUUID()
    : `staff-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function buildAssignmentCommand(
  input: AssignmentCommand
): AssignmentCommand {
  if (!input.reason.trim()) throw new Error('Assignment reason is required');
  if (input.override_reason !== undefined && !input.override_reason.trim()) {
    throw new Error('Override reason is required');
  }
  return {
    ...input,
    reason: input.reason.trim(),
    ...(input.required_trades !== undefined
      ? { required_trades: [...new Set(input.required_trades)] }
      : {}),
    ...(input.override_reason !== undefined
      ? { override_reason: input.override_reason.trim() }
      : {})
  };
}

/** Omit new personnel fields entirely for callers using the legacy create contract. */
export function personnelPlanning(input: {
  canAssign: boolean;
  canPlan?: boolean;
  assignedTo: number | null;
  ownerKey?: 'assigned_to' | 'assigned_to_id';
  requiredTrades: string[];
  confirmed: boolean;
  legacyAssignee: string;
  overrideReason?: string;
}) {
  const assignment = input.canAssign && input.assignedTo != null;
  const requirements =
    input.canPlan && (input.requiredTrades.length > 0 || input.confirmed);
  if (!assignment && !requirements) {
    return { assignee: input.legacyAssignee.trim() };
  }
  return {
    ...(assignment
      ? { [input.ownerKey ?? 'assigned_to']: input.assignedTo }
      : {}),
    ...(requirements
      ? {
          required_trades: input.requiredTrades,
          trade_requirements_confirmed: input.confirmed
        }
      : {}),
    ...(input.overrideReason
      ? { override_reason: input.overrideReason.trim() }
      : {})
  };
}
