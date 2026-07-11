/**
 * TypeScript types for the Assets (Equipment Machines) feature.
 */

export interface AssetMachine {
  pk: number;
  name: string;
  description: string;
  active: boolean;
  location: string;
  customer: number | null;
  customer_name: string | null;
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
  work_order: number | null;
  work_order_title: string | null;
  created_at: string;
  updated_at: string;
}
