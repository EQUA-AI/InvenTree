export interface RepairScopeDraftSource {
  id: number;
  title: string;
  filename: string;
  sha256: string;
}

export interface RepairScopeDraft {
  id: number;
  revision: number;
  work_order_id: number;
  machine_id: number;
  repair_packet_id: number;
  source_event_id: number;
  component_code: string;
  actor_id: number | null;
  saved_at: string;
  lifecycle_version: number;
  status: 'proposed';
  gates: {
    supervisor_review: 'pending';
    site_safety_authorization: 'pending';
    physical_execution: 'not_started';
    post_repair_verification: 'not_started';
  };
  scope: {
    suspected_cause: string;
    steps: string[];
    tools_and_supplies: string[];
    required_ppe: string[];
    isolation_checks: string[];
    post_work_checks: string[];
    replacement_parts: never[];
    parts_decision: string;
    open_questions: string[];
  };
  sources: {
    e03: RepairScopeDraftSource & {
      label: 'P08-TEMP-E03';
      prepared: true;
    };
    manuals: (RepairScopeDraftSource & {
      applicability: 'related_reference_unverified';
    })[];
  };
}

export interface RepairScopeDraftResponse {
  work_order_id: number;
  machine_id: number | null;
  repair_packet_id: number | null;
  lifecycle_version: number;
  draft: RepairScopeDraft | null;
}
