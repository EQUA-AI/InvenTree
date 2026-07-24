export type KanbanStatus = string;

export type KanbanPriority = 'low' | 'medium' | 'high';

export type AllocationStatus = 'none' | 'partial' | 'full' | 'insufficient';

export interface KanbanCardPart {
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

export interface KanbanCard {
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
  parts: KanbanCardPart[];
}

export type KanbanCardPayload = Omit<
  KanbanCard,
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
