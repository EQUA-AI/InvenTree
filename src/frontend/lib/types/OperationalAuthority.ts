/** O15 native records: source clocks, recorder and independent facts. */
export type AuthorityKind =
  | 'isolation'
  | 'permit'
  | 'access_window'
  | 'test_acceptance'
  | 'operating_permission'
  | 'observed_return'
  | 'service_state';

export interface AuthorityRecord {
  id: number;
  work_order_id: number;
  version: number;
  kind: AuthorityKind;
  outcome: string;
  statement: string;
  source_reference: string;
  recorded_by: string | null;
  recorded_by_id: number;
  recorded_at: string;
  observed_at: string;
  effective_from: string;
  effective_until: string | null;
  basis_state?: 'current' | 'changed_or_unrecorded';
  effective_state: string;
  current: boolean;
  supersedes: number | null;
  gate_id: number | null;
  verified_by: string | null;
  verified_by_id: number | null;
  gate_name: string | null;
  target_name: string | null;
  verified_at: string | null;
  target_id: number | null;
  details: Partial<
    Record<'duty' | 'standby' | 'redundancy' | 'restrictions', string | null>
  >;
  basis: { telemetry?: AuthorityTelemetry; [key: string]: unknown };
}

export interface AuthorityTelemetry {
  reading_token: string;
  binding_id: number;
  target_id: number;
  target_name: string;
  tag: string;
  path: string;
  signal_kind: string;
  source: string;
  source_id: number;
  source_sequence: number | null;
  source_active: boolean;
  value: unknown;
  observed_at: string | null;
  quality: string;
  stale: boolean | null;
}

export interface OperationalAuthority {
  rule: 'operational_authority.v1';
  work_order_id: number;
  work_version: number;
  context_token: string;
  commands: Partial<Record<AuthorityKind, boolean>>;
  gates: {
    id: number;
    name: string;
    requires_second_person: boolean;
    can_record: boolean;
  }[];
  targets: { id: number; name: string }[];
  physical: AuthorityRecord[];
  operational: AuthorityRecord[];
  telemetry: AuthorityTelemetry[];
}
