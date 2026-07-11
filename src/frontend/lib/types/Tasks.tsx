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
