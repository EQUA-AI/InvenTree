type MetricRecord = {
  pk: number;
  model: 'workorder' | 'assetmachine';
  label: string;
  machine_label?: string;
  assigned_user?: string;
  data_health?: string;
  parts_gap?: boolean;
  linked_orders?: { pk: number; label: string }[];
  visible_machine?: number;
  due_date?: string;
  lifecycle_status?: string;
  priority?: string;
  actual_completed_at?: string;
  created_at?: string;
  verifying_since?: string;
  minutes?: number;
  amended?: boolean;
  hold_reason?: string;
  alert_count?: number;
  first_observed_at?: string;
  last_observed_at?: string;
};
export type MaintenanceResult = {
  id: string;
  version: number;
  state: 'ready' | 'unavailable';
  complete: boolean;
  value: number | null;
  unit: string;
  record_count: number;
  detail_count: number;
  groups: {
    key: string;
    label: string;
    count: number;
    value: number;
    high_priority?: number;
    overdue?: number;
  }[];
  missing: Record<string, number>;
  stats: Record<string, number | string | null>;
  records: MetricRecord[];
  next_offset: number | null;
  observed_at: string;
  timezone: string;
  from: string;
  to: string;
  partial_period: boolean;
  clock: string;
  reporting_date: string;
  machines: { value: string; label: string }[];
  clients: { value: string; label: string }[];
  assignees: { value: string; label: string }[];
};

export function parseMaintenanceResult(
  data: MaintenanceResult,
  id: string
): MaintenanceResult {
  if (
    data?.id !== id ||
    data.version !== 1 ||
    !['ready', 'unavailable'].includes(data.state)
  )
    throw new Error('Invalid metric response');
  if (data.state === 'unavailable') return data;
  if (
    !data.complete ||
    !Number.isSafeInteger(data.record_count) ||
    data.record_count < 0 ||
    (data.value !== null && (!Number.isFinite(data.value) || data.value < 0)) ||
    !Array.isArray(data.records) ||
    !Array.isArray(data.groups) ||
    !Number.isSafeInteger(data.detail_count) ||
    data.detail_count < 0 ||
    data.records.length > data.detail_count ||
    (data.next_offset !== null &&
      (!Number.isSafeInteger(data.next_offset) ||
        data.next_offset <= 0 ||
        data.next_offset >= data.detail_count)) ||
    data.records.some(
      (r) =>
        !Number.isSafeInteger(r.pk) ||
        r.pk <= 0 ||
        !['workorder', 'assetmachine'].includes(r.model)
    )
  )
    throw new Error('Incomplete metric response');
  return data;
}
