import type { CloseoutReadingRow } from './Closeout';
import type { PackageStep } from './WorkExecution';

/** Existing scoped records; no calibration-specific phase/instrument provider. */
export interface RoutineWork {
  rule: 'routine_work.v1';
  work_order_type: string;
  can_create_linked?: boolean;
  create_unavailable_reason?:
    | 'permission'
    | 'routine_scope'
    | 'machine_scope'
    | 'routine_inactive_or_canceled'
    | null;
  linked_corrective?: RoutineCorrectiveLink[];
  procedures_state: 'read' | 'not_enabled';
  readings_state: 'read' | 'not_enabled';
  applications: {
    id: number;
    revision: number | null;
    snapshot_hash: string;
    applied_by: string | null;
    applied_at: string;
    tasks: {
      id: number;
      step: PackageStep;
      status: string;
      version: number;
      value: unknown;
      note: string;
      disposition_reason: string;
      recorded_by: string | null;
      recorded_at: string | null;
    }[];
  }[];
  readings: (CloseoutReadingRow & {
    step_execution: number | null;
    expected_min: string | null;
    expected_max: string | null;
    disposition_reason: string;
  })[];
}

export interface RoutineCorrectiveLink {
  id: number;
  reference: string;
  title?: string;
  finding: string;
  reading_id: number | null;
  step_id: number | null;
}
export type RoutineOrigin =
  | { state: 'unavailable' }
  | (RoutineCorrectiveLink & { state: 'available' });
