export interface StaffTrade {
  pk: number;
  code: string;
  name: string;
  active: boolean;
}

export interface StaffMember {
  user_id: number;
  display_name: string;
  job_title: string | null;
  trades: StaffTrade[];
}

export interface StaffPage<T> {
  count: number;
  results: T[];
}

export type StaffScope =
  | { workOrderId: number; machineId?: never; kanbanCardId?: number }
  | { machineId: number; workOrderId?: never };

export interface OccupationalProfile {
  position: string | null;
  trades: number[];
}

export interface AssignmentCommand {
  work_order_id: number;
  assigned_to: number | null;
  expected_version: number;
  idempotency_key: string;
  reason: string;
  override_reason?: string;
  required_trades?: string[];
  trade_requirements_confirmed?: boolean;
  kanban_card_id?: number;
}
