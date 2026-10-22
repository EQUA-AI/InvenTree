/**
 * R6 P12 / UI08: a machine's fault occurrences (`fault_occurrences.v1`).
 *
 * An occurrence is an existing anomaly record: its own id, the source's alarm
 * id and code, the evidence snapshots retained for it and the work that owns
 * the response. The UI never mints a Fault record or identifier, and never
 * shows a current reading in place of a retained one.
 */

import type {
  AnomalySeverity,
  AnomalySignalRef,
  AnomalyStatus,
  SignalQuality
} from '@lib/types/MachineHealth';

export const OCCURRENCE_PAGE_SIZE = 25;

export type OccurrenceStatusFilter = 'all' | 'active' | 'resolved';

export interface OccurrenceWork {
  id: number;
  reference: string | null;
  title: string;
  lifecycle_status: string;
  priority: string;
  work_order_type: string;
  affected_component: string;
  actual_completed_at: string | null;
  relation: 'owning' | 'repair_packet';
}

export interface RetainedSample {
  observed_at: string | null;
  value: unknown;
}

export interface RetainedSnapshot {
  recorded_telemetry_as_live?: boolean;
  id: string;
  signal_label: string;
  unit: string;
  binding: number | null;
  source_name: string | null;
  window_start: string | null;
  window_end: string | null;
  captured_at: string;
  captured_by_name?: string | null;
  system_actor?: string;
  quality: SignalQuality;
  stale: boolean;
  reason: string;
  content_hash: string;
  display_shifted: boolean;
  display_shift_seconds: number;
  /** List rows carry the count; the detail carries the samples. */
  sample_count?: number;
  samples?: RetainedSample[];
  statistics?: Record<string, unknown>;
}

export interface OccurrenceComponent {
  code: string;
  name: string;
  status: string;
}

export interface FaultOccurrence {
  recorded_telemetry_as_live?: boolean;
  id: number;
  external_id: string;
  alarm_code: string;
  severity: AnomalySeverity;
  status: AnomalyStatus;
  active: boolean;
  title: string;
  evidence_summary: string;
  metrics: Record<string, unknown>;
  detector: string;
  detector_version: string;
  source: { id: number; name: string; type: string } | null;
  signals: AnomalySignalRef[];
  components: OccurrenceComponent[];
  first_observed_at: string | null;
  last_observed_at: string | null;
  display_shifted: boolean;
  display_shift_seconds: number;
  acknowledged_at: string | null;
  acknowledged_by_name: string | null;
  resolved_at: string | null;
  resolution_note: string;
  work: OccurrenceWork[];
  repair_packet: { id: number; reference: string; status: string } | null;
  snapshot_count: number;
  snapshots: RetainedSnapshot[];
}

export interface OccurrencePage {
  recorded_telemetry_as_live?: boolean;
  plant_timezone?: string | null;
  rule: string;
  count: number;
  limit: number;
  offset: number;
  results: FaultOccurrence[];
}

export const OCCURRENCE_STATUS_PARAM = 'fault_status';
export const OCCURRENCE_PAGE_PARAM = 'fault_page';
/** Opens one occurrence's evidence: the link a fault or diagnosis can carry. */
export const OCCURRENCE_PARAM = 'occurrence';

export interface OccurrenceListState {
  status: OccurrenceStatusFilter;
  page: number;
  open: number | null;
}

function positive(raw: string | null): number | null {
  return raw && /^[1-9][0-9]{0,9}$/.test(raw) ? Number(raw) : null;
}

/** The list state from the URL; unknown values fall back to the defaults. */
export function occurrenceListState(
  params: URLSearchParams
): OccurrenceListState {
  const raw = params.get(OCCURRENCE_STATUS_PARAM);
  return {
    status: raw === 'active' || raw === 'resolved' ? raw : 'all',
    page: positive(params.get(OCCURRENCE_PAGE_PARAM)) ?? 1,
    open: positive(params.get(OCCURRENCE_PARAM))
  };
}

export function occurrenceListParams(
  previous: URLSearchParams,
  state: OccurrenceListState
): URLSearchParams {
  const next = new URLSearchParams(previous);
  next.delete(OCCURRENCE_STATUS_PARAM);
  next.delete(OCCURRENCE_PAGE_PARAM);
  next.delete(OCCURRENCE_PARAM);
  if (state.status !== 'all') next.set(OCCURRENCE_STATUS_PARAM, state.status);
  if (state.page > 1) next.set(OCCURRENCE_PAGE_PARAM, String(state.page));
  if (state.open !== null) next.set(OCCURRENCE_PARAM, String(state.open));
  return next;
}

export function occurrenceRequest(
  state: OccurrenceListState
): Record<string, string | number> {
  return {
    status: state.status,
    limit: OCCURRENCE_PAGE_SIZE,
    offset: (state.page - 1) * OCCURRENCE_PAGE_SIZE
  };
}

/** A display shift as whole days, hours and minutes; null when none. */
export function shiftParts(
  seconds: number
): { days: number; hours: number; minutes: number } | null {
  if (!seconds) return null;
  const total = Math.abs(Math.round(seconds / 60));
  return {
    days: Math.floor(total / 1440),
    hours: Math.floor((total % 1440) / 60),
    minutes: total % 60
  };
}
