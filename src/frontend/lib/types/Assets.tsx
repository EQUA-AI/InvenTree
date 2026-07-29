/**
 * TypeScript types for the Assets (Equipment Machines) feature.
 */

export interface AssetMachine {
  pk: number;
  name: string;
  description: string;
  active: boolean;
  location: string;
  /**
   * Tenant of this software; how a machine resolves its scope. System-only:
   * never displayed on the frontend.
   */
  client: number | null;
  manufacturer: string;
  model: string;
  serial: string;
  created_at: string;
  updated_at: string;
}

export interface MachinePart {
  pk: number;
  machine: number;
  part: number;
  part_name: string;
  quantity: number;
  notes: string;
}

export interface AssetMaintenanceRecord {
  pk: number;
  machine: number;
  date: string;
  summary: string;
  details: string;
  performed_by: string;
  /**
   * Linked completed work order. Null for genuinely unowned legacy history, and
   * also when the caller may read the machine history but not the work order -
   * the id is withheld rather than rendered as a dead link.
   */
  work_order: number | null;
  work_order_reference: string | null;
  work_order_title: string | null;
  work_order_type: string | null;
  lifecycle_status: string | null;
  actual_completed_at: string | null;
  downtime_minutes: number | null;
  verified: boolean;
  follow_up_required: boolean;
  created_at: string;
  updated_at: string;
}

export interface AssetClient {
  pk: number;
  name: string;
  code: string;
  active: boolean;
  machine_count: number;
  created_at: string;
  updated_at: string;
}
