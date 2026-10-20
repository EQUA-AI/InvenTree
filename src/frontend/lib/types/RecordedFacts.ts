export interface CriticalDesignation {
  applies_current?: boolean;
  review_required?: boolean;
  critical: boolean;
  version: number;
  reason: string;
  recorded_by: string | null;
  recorded_at: string;
}
export interface FittedRecord {
  retracted_at?: string | null;
  retracted_by?: string | null;
  retraction_reason?: string | null;
  id: number;
  stock_item: number;
  serial: string | null;
  lot: string | null;
  fitted_at: string | null;
  recorded_by_id?: number;
  work_order: number | null;
  recorded_by: string | null;
  recorded_at: string;
  verified_by: string | null;
  verified_at: string | null;
  source: string;
  reason: string;
}
export interface FitProjection {
  retracted?: FittedRecord[];
  version: number;
  current: FittedRecord | null;
  history: FittedRecord[];
  pending: FittedRecord[];
}
export interface TimeRecords {
  closed_at?: string | null;
  open_at_closure?: boolean;
  observed_at: string;
  coverage: 'partial' | 'unknown';
  recorded_seconds: number | null;
  intervals: {
    id: number;
    person_id: number;
    person: string | null;
    started_at: string;
    ended_at: string | null;
    end_action: string | null;
    recorded_by: string | null;
    recorded_at: string;
    ended_by: string | null;
    ended_recorded_at: string | null;
    reason: string | null;
    supersedes: number | null;
    superseded: boolean;
  }[];
  gaps: { person: string | null; started_at: string; ended_at: string }[];
}
export interface DowntimeRecord {
  id: number;
  binding_id: number;
  signal: string;
  started_at: string;
  ended_at: string;
  recorded_by: string | null;
  recorded_at: string;
  reason: string | null;
  supersedes: number | null;
  superseded: boolean;
}
export interface TimeResult {
  version: number;
  time: TimeRecords;
  downtime: DowntimeRecord[];
}
export interface DowntimeProposal {
  token: string;
  started_at: string;
  ended_at: string;
  signal: string;
  binding_id: number;
}
