/**
 * TypeScript types for the Assets (Equipment Machines) feature.
 */

export interface AssetMachine {
  pk: number;
  name: string;
  description: string;
  active: boolean;
  /** Plain equipment, or a registered pump station or one of its pumps. */
  asset_type: 'equipment' | 'pumphouse' | 'pump';
  /** A pump's station. Null for a station and for plain equipment. */
  parent: number | null;
  parent_name: string | null;
  /** Which bay of its station a pump is, e.g. `P5`. Blank for equipment. */
  source_key: string;
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
  part_group: string;
  quantity: number;
  notes: string;
}

/**
 * One component the equipment registry holds for a pump or a station.
 *
 * Not the same record as a MachinePart. A MachinePart says a part is fitted,
 * and how many; a component is a place in the machine that the registry has
 * identified - from the station's source tags, until someone verifies it.
 */
export interface MachineComponent {
  pk: number;
  uuid: string;
  /** The pump or station that owns the component. */
  machine: number;
  machine_name: string;
  /** The catalogue part this component is an occurrence of. */
  part: number;
  part_name: string;
  /** The catalogue part is a logical group rather than a physical item. */
  virtual: boolean;
  code: string;
  name: string;
  status: 'draft' | 'verified';
  provenance: string;
  review_note: string;
  reviewed_at: string | null;
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
