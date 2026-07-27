/**
 * TypeScript types for the Machine Health feature.
 *
 * Mirrors machine_health/serializers.py. Everything here is read-only: health
 * state is written by connectors and deterministic detectors, never by the UI.
 */

export type HealthState =
  | 'unknown'
  | 'normal'
  | 'warning'
  | 'critical'
  | 'offline';

export type SignalQuality = 'good' | 'uncertain' | 'bad' | 'unknown';

export type AnomalySeverity = 'info' | 'warning' | 'critical';

export type AnomalyStatus = 'open' | 'acknowledged' | 'resolved' | 'suppressed';

export type HealthSourceType =
  | 'iot'
  | 'scada'
  | 'plc'
  | 'dcs'
  | 'mes'
  | 'bas_bms'
  | 'ems'
  | 'iiot'
  | 'historian'
  | 'webhook'
  | 'manual';

export interface HealthSourceStatus {
  source_id: number;
  name: string;
  source_type: HealthSourceType;
  active: boolean;
  healthy: boolean;
  last_success_at: string | null;
  last_error_at: string | null;
  /** Redacted classification only; connector messages never reach the client. */
  last_error_code: string;
  freshness_threshold_seconds: number;
  mapped_tag_count: number;
}

export interface MachineHealthSummary {
  state: HealthState;
  /** False when no source is mapped: an empty state, not a healthy machine. */
  configured: boolean;
  signal_count: number;
  stale_signal_count: number;
  degraded_data: boolean;
  last_observed_at: string | null;
  anomaly_counts: Record<AnomalySeverity, number>;
  active_anomaly_count: number;
  sources: HealthSourceStatus[];
}

export interface MachineSignalLimits {
  normal_min: number | null;
  normal_max: number | null;
  warn_min: number | null;
  warn_max: number | null;
  critical_min: number | null;
  critical_max: number | null;
}

export interface MachineSignal {
  binding_id: number;
  source_id: number;
  source_name: string;
  source_type: HealthSourceType;
  display_name: string;
  signal_kind: string;
  unit: string;
  value: number | string | boolean | null;
  observed_at: string | null;
  received_at: string | null;
  quality: SignalQuality;
  stale: boolean;
  freshness_threshold_seconds: number;
  /** 'unknown' whenever the row is stale: a stale value has no verdict. */
  state: HealthState;
  limits: MachineSignalLimits;
}

export interface AnomalySignalRef {
  binding_id: number;
  display_name: string;
  unit: string;
  signal_kind: string;
}

export interface MachineAnomaly {
  pk: number;
  machine: number;
  source: number | null;
  source_name: string | null;
  source_type: HealthSourceType | null;
  external_id: string;
  alarm_code: string;
  fingerprint: string;
  severity: AnomalySeverity;
  status: AnomalyStatus;
  title: string;
  evidence_summary: string;
  metrics: Record<string, unknown>;
  detector: string;
  detector_version: string;
  signals: AnomalySignalRef[];
  first_observed_at: string;
  last_observed_at: string;
  acknowledged_at: string | null;
  acknowledged_by_name: string | null;
  acknowledgement_note: string;
  resolved_at: string | null;
  resolution_note: string;
  work_order: number | null;
  repair_packet: number | null;
  created_at: string;
  updated_at: string;
}

export interface HealthEvidenceSnapshot {
  id: string;
  machine: number;
  anomaly: number | null;
  source: number | null;
  binding: number | null;
  signal_label: string;
  unit: string;
  window_start: string;
  window_end: string;
  captured_at: string;
  samples: { observed_at: string; value: unknown }[];
  statistics: Record<string, unknown>;
  quality: SignalQuality;
  stale: boolean;
  reason: string;
  source_references: Record<string, unknown>;
  content_hash: string;
  system_actor: string;
}

export type AnalysisStatus =
  | 'available'
  | 'unavailable'
  | 'stale'
  | 'insufficient';

export type EvidenceRelation = 'supports' | 'contradicts' | 'unknown';

export interface PreliminaryEvidence {
  snapshot_id: string | null;
  observation: string;
  relation: EvidenceRelation;
  signal_label?: string;
  unit?: string;
  observed_at?: string | null;
  quality?: SignalQuality;
  stale?: boolean;
}

/**
 * Diagnosis schema v2. Preliminary until `verified_by_user` is true - the UI
 * must label it "Preliminary results" up to that point, never "Diagnosis".
 */
export interface PreliminaryResults {
  status: AnalysisStatus;
  likely_cause: string;
  failure_mode: string | null;
  confidence: number;
  confidence_label: string;
  alternatives: string[];
  evidence: PreliminaryEvidence[];
  confirm_tests: string[];
  data_window: {
    start: string | null;
    end: string | null;
    snapshot_count: number;
  };
  freshness: { stale: boolean; stale_signal_count: number };
  quality: { summary: string; bad_signal_count: number };
  provider: string;
  model_or_rule_version: string;
  generated_at: string | null;
  verified_by_user: boolean;
  verified_at: string | null;
  amendments: unknown[];
  schema_version: number;
}
