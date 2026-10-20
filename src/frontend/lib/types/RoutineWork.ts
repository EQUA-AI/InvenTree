import type { CloseoutReadingRow } from './Closeout';
import type { PackageStep } from './WorkExecution';

export interface CalibrationTraceability {
  id: number;
  work_order: number;
  reading: number;
  revision: number;
  supersedes: number | null;
  reference_instrument_id: string;
  certificate_number: string;
  issuing_lab: string;
  valid_until: string | null;
  validity_state: 'expired_at_reading' | 'within_date_at_reading' | 'unknown';
  validity_timezone: string;
  reading_recorded_at: string | null;
  certificate_id: number | null;
  certificate_snapshot: {
    id: number;
    filename: string;
    comment: string;
    sha256?: string;
    size?: number;
  };
  certificate_integrity_state?:
    | 'unchanged'
    | 'changed'
    | 'unavailable'
    | 'identity_not_recorded';
  certificate_available: boolean;
  certificate_url: string | null;
  correction_reason: string;
  recorded_by: number;
  recorded_by_name: string | null;
  recorded_at: string;
}

/** Additive v1 extension; older servers cannot authorize a traceability writer. */
export interface RoutineWork {
  rule: 'routine_work.v1';
  calibration_traceability_can_record?: boolean;
  work_order_type: string;
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
    calibration_traceability?: CalibrationTraceability[];
    step_execution: number | null;
    expected_min: string | null;
    expected_max: string | null;
    disposition_reason: string;
  })[];
}
