/**
 * TypeScript types for the complete work-order overview.
 *
 * Mirrors `WorkOrderOverviewSerializer`. The page renders every applicable
 * section from this one read rather than a request per section.
 */

import type { BoardCard, WorkOrder } from './Tasks';

export interface WorkOrderSummary {
  id: number;
  reference: string | null;
  title: string;
  description: string;
  status: string;
  priority: string;
  lifecycle_status: string;
  work_order_type: string;
  machine: number | null;
  machine_name: string | null;
  assigned_to: number | null;
  assigned_to_name: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  estimated_minutes: number | null;
  is_active: boolean;
}

export interface WorkOrderDependency {
  id: number;
  direction: 'predecessor' | 'successor';
  dependency_type: string;
  lag_minutes: number;
  card: WorkOrderSummary;
}

export interface WorkOrderEvent {
  id: number;
  event_type: string;
  from_status: string;
  to_status: string;
  reason: string;
  created_at: string;
}

export interface RepairGate {
  id: number;
  name: string;
  gate_type: string;
  status: string;
  is_blocking: boolean;
  is_mandatory: boolean;
  requires_photo: boolean;
  requires_second_person: boolean;
}

export interface OverviewFinding {
  id: number;
  finding_key: string;
  category: string;
  observation: string;
  value: number | null;
  unit: string;
  evidence_source: string;
  /** Cites an immutable health evidence snapshot when the source is telemetry. */
  snapshot_id: string | null;
  observed_at: string | null;
  verification: string;
}

export interface OverviewApprovedScope {
  id: number;
  version: number;
  verified_cause: string;
  scope_lines: { sequence: number; action: string }[];
  failure_codes: string[];
  crew_size: number | null;
  planned_elapsed_minutes: number | null;
  approved_at: string;
  approval_note: string;
}

export interface RepairPacketOverview {
  id: number;
  reference: string;
  status: string;
  criticality: string;
  fault_summary: string;
  symptom: string;
  production_impact: string;
  generation_status: string;
  diagnosis: Record<string, unknown>;
  /** True until a human verifies it; the UI must say "Preliminary results". */
  diagnosis_is_preliminary: boolean;
  diagnosis_status: string | null;
  findings: OverviewFinding[];
  approved_scope: OverviewApprovedScope | null;
  gates: RepairGate[];
}

/** The health anomaly this work order answers, when it was raised from one. */
export interface WorkOrderSourceAlert {
  id: number;
  title: string;
  severity: string;
  status: string;
  alarm_code: string;
  external_id: string;
  detector: string;
  detector_version: string;
  evidence_summary: string;
  first_observed_at: string;
  last_observed_at: string;
  source_name: string | null;
  source_type: string | null;
  machine_id: number;
}

export interface MaintenanceRecordOverview {
  id: number;
  date: string;
  summary: string;
  details: string;
  performed_by: string;
}

export interface StructuredCloseoutOverview {
  id: number;
  cause: string;
  action: string;
  result: string;
  verification_summary: string;
  downtime_minutes: number | null;
  follow_up_required: boolean;
  follow_up: string;
  completed_at: string;
  verified_at: string | null;
  /**
   * Values reflect the latest applied closeout amendment, not the immutable
   * base row; `amended` makes the governed correction visible.
   */
  amended: boolean;
  amendment_count: number;
}

export interface WorkOrderOverview extends WorkOrder {
  /** Every tracked piece of this job, in board order. */
  cards: BoardCard[];
  dependencies: WorkOrderDependency[];
  events: WorkOrderEvent[];
  repair_packet: RepairPacketOverview | null;
  source_alert: WorkOrderSourceAlert | null;
  maintenance_record: MaintenanceRecordOverview | null;
  structured_closeout: StructuredCloseoutOverview | null;
  canonical_commands_enabled: boolean;
}
