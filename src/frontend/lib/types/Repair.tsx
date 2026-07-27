/**
 * TypeScript types for the Repair Packet (spine) feature.
 */

export interface LockoutPoint {
  pk: number;
  gate: number;
  energy_source: string;
  isolation_device: string;
  lock_id: string;
  tag_id: string;
  status: string;
  applied_by: number | null;
  verified_by: number | null;
  verified_at: string | null;
  restored_at: string | null;
  note: string;
  created_at: string;
  updated_at: string;
}

export interface SafetyEvidenceProof {
  pk: number;
  gate: number;
  lockout_point: number | null;
  proof_type: string;
  value: Record<string, unknown>;
  captured_by: number | null;
  captured_at: string;
}

export interface SafetyGateTemplate {
  pk: number;
  name: string;
  gate_type: string;
  instructions: string;
  applies_to: Record<string, unknown>;
  required_permission: string;
  requires_photo: boolean;
  requires_second_person: boolean;
  is_blocking: boolean;
  is_mandatory: boolean;
  risk_tier: number;
  default_sequence: number;
  active: boolean;
  created_at: string;
  updated_at: string;
}

export interface RepairPacketGate {
  pk: number;
  template: number | null;
  sequence: number;
  is_blocking: boolean;
  is_mandatory: boolean;
  name: string;
  gate_type: string;
  status: string;
  requires_photo: boolean;
  required_permission: string;
  requires_second_person: boolean;
  confirmed_by: number | null;
  confirmed_at: string | null;
  verified_by: number | null;
  verified_at: string | null;
  waived_by: number | null;
  waived_at: string | null;
  waiver_reason: string;
  waiver_authority: string;
  note: string;
  lockout_points: LockoutPoint[];
  proofs: SafetyEvidenceProof[];
  unsatisfied_reason: string;
  created_at: string;
}

export interface RepairPacketPart {
  id: number;
  part: number;
  part_name: string;
  part_ipn: string;
  quantity: number;
  allocated_quantity: number;
  allocation_status: string;
  allocation_note: string;
}

export interface RepairPacketApproval {
  pk: string;
  purpose: string;
  status: string;
}

export interface RepairPacketEvent {
  pk: number;
  event_type: string;
  from_status: string;
  to_status: string;
  actor: number | null;
  reason: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface RepairPacketGenerationRun {
  pk: number;
  agent_run_id: string;
  provider: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  error: string;
  result_summary: Record<string, unknown>;
}

export interface RepairPacket {
  pk: number;
  reference: string;
  status: string;
  status_label: string;
  machine: number | null;
  machine_name: string;
  fault_summary: string;
  symptom: string;
  criticality: string;
  production_impact: string;
  diagnosis: Record<string, unknown>;
  diagnosis_schema_version: number;
  generation_status: string;
  work_order: number | null;
  work_order_reference: string | null;
  /** Optimistic-concurrency token the close command must echo back. */
  work_order_lifecycle_version: number | null;
  parts: RepairPacketPart[];
  gates: RepairPacketGate[];
  unsatisfied_safety_gates: Array<{ pk: number; name: string; reason: string }>;
  evidence: unknown[];
  events: RepairPacketEvent[];
  approvals: RepairPacketApproval[];
  latest_generation_run: RepairPacketGenerationRun | null;
  closeout: Record<string, unknown>;
  agent_run_id: string;
  created_by: number | null;
  created_at: string;
  updated_at: string;
}
