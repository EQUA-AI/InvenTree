export type KanbanStatus = string;

export type KanbanPriority = 'low' | 'medium' | 'high';

export type AllocationStatus = 'none' | 'partial' | 'full' | 'insufficient';

export interface WorkOrderPart {
  id: number;
  part: number;
  part_name: string;
  part_ipn: string;
  part_thumbnail: string | null;
  quantity: number;
  allocated_quantity: number;
  allocation_status: AllocationStatus;
  allocation_note: string;
  created_at: string;
  updated_at: string;
}

export interface WorkOrder {
  id: number;
  title: string;
  description: string;
  status: KanbanStatus;
  priority: KanbanPriority;
  due_date: string | null;
  assignee: string;
  machine: number | null;
  machine_name: string | null;
  machine_location: string | null;
  assigned_to: number | null;
  assigned_to_username: string | null;
  assigned_to_name: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  estimated_minutes: number | null;
  work_order_type: string;
  reference: string | null;
  lifecycle_status: string;
  lifecycle_version: number;
  actual_started_at: string | null;
  actual_completed_at: string | null;
  parent: number | null;
  card_kind: string;
  tags: string[];
  company: string;
  company_contact_name: string;
  company_contact_phone: string;
  job_number: string;
  service_quote: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  parts: WorkOrderPart[];
}

/**
 * One tracked piece of a work order, as it appears on the board.
 *
 * A job is authorised once - one `WorkOrder` - but is usually worked through
 * several cards: diagnose, source the part, do the repair. A card owns where it
 * sits on the board and who is doing that piece; the job owns its reference,
 * its lifecycle and its closeout.
 *
 * The `work_order_*`, `priority`, `lifecycle_*` and `machine*` fields are the
 * job's, copied in so the board renders in one request. They are read-only:
 * changing the job means calling the work-order endpoints.
 *
 * `assigned_to` and the schedule may be null, which means "whatever the job
 * says" rather than "nobody" and "never" - `effective_*` is that already
 * resolved.
 */
export interface BoardCard {
  id: number;
  work_order: number;
  card_kind: string;
  status: KanbanStatus;
  board_order: number;
  title: string;
  description: string;
  assigned_to: number | null;
  assignee: string;
  scheduled_start: string | null;
  scheduled_end: string | null;
  estimated_minutes: number | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  work_order_reference: string | null;
  work_order_title: string | null;
  priority: KanbanPriority | null;
  lifecycle_status: string | null;
  lifecycle_version: number | null;
  machine: number | null;
  machine_name: string | null;
  tags: string[];
  effective_assignee: number | null;
  effective_start: string | null;
  effective_end: string | null;
}

export type WorkOrderPayload = Omit<
  WorkOrder,
  'id' | 'created_at' | 'updated_at' | 'is_active' | 'parts'
>;

export interface KanbanColumnRecord {
  id: number;
  key: string;
  label: string;
  color: string;
  order: number;
  is_default: boolean;
  card_count: number;
  created_at: string;
  updated_at: string;
}
