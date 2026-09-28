/*
 * Pure query/cache/availability logic for the synthetic demo metrics panel.
 *
 * Rules encoded here (and pinned by demoMetrics.test.ts):
 * - Query keys carry identity, session, location, descendant mode and time
 *   window, so a change to any of them never reuses another scope's data.
 * - Denied access (401/403) drops every cached demo query immediately; a
 *   sign-out clears the whole cache elsewhere (UserState) — after either,
 *   no demo data survives.
 * - A failed request is an error state, never a fixture fallback, and
 *   "capability unavailable" is distinct from numeric zero.
 * - Chart series keep `null` for missing coverage; zero is only ever the
 *   value the API reported.
 */

export const demoMetricsApi = '/api/assets/demo-metrics/';

export interface DemoSessionSummary {
  id: string;
  dataset_key: string;
  session_key: string;
  mode: string;
  status: string;
  synthetic: boolean;
  anchor_at: string | null;
  expires_at: string | null;
}

export interface DemoMetrics {
  mode: string;
  synthetic: boolean;
  session: { id: string; session_key: string; status: string };
  generated_at: string;
  filters: { location_id: number | null; include_descendants: boolean };
  cohort_size: number;
  machines: {
    alias: string;
    machine_id: number;
    coverage_state: string;
    condition: string | null;
  }[];
  observation_coverage: Record<string, number>;
  fresh_condition: Record<string, number>;
  open_work_orders: number;
  machines_with_open_work: number;
  overdue_open_work_orders: number;
  open_by_state: Record<string, number>;
  capabilities: {
    history_available: boolean;
    replay_available: boolean;
    anomaly_creation: boolean;
    oee: boolean;
  };
}

export interface DemoWorkRow {
  id: number;
  reference: string | null;
  title: string;
  machine_id: number;
  priority: string;
  lifecycle_status: string;
  is_active: boolean;
  open: boolean;
  due_date: string | null;
  overdue: boolean;
}

export interface DemoWorkList {
  count: number;
  open_count: number;
  /** Rows actually included in ``results`` (bounded server-side). */
  results_returned?: number;
  /** True when the server truncated the row list; counts remain full totals. */
  has_more?: boolean;
  filters: { location_id: number | null; include_descendants: boolean };
  session: { id: string; session_key: string; synthetic: boolean };
  results: DemoWorkRow[];
}

export interface DemoHistoryBucket {
  date: string;
  planned_machine_minutes: number;
  downtime_machine_minutes: number | null;
  contributing_interval_ids: string[];
}

export interface DemoHistory {
  available?: boolean;
  reason?: string;
  synthetic: boolean;
  attribution_mode?: string;
  session_key?: string;
  filters?: { location_id: number | null; include_descendants: boolean };
  window?: { start: string; end: string; timezone: string };
  selected_machines: number;
  measured_machines: number;
  completeness: string;
  daily: DemoHistoryBucket[];
  planned_machine_minutes: number;
  downtime_machine_minutes: number | null;
  measured_cohort_availability: number | null;
}

export interface DemoScope {
  identity: number | string;
  session: string | null;
  location?: number | null;
  descendants: boolean;
  window?: { start: string; end: string } | null;
}

export function demoQueryKeys(scope: DemoScope) {
  // Every key carries identity, session, location, descendant mode and the
  // time window, so a change to any of them never reuses another scope's
  // data. A query that is not windowed passes the window slot as null; the
  // dimension is still explicit instead of silently absent.
  const time = [scope.window?.start ?? null, scope.window?.end ?? null];
  return {
    sessions: ['demo-metrics', scope.identity, 'sessions'] as const,
    metrics: [
      'demo-metrics',
      scope.identity,
      'metrics',
      scope.session,
      scope.location ?? null,
      scope.descendants,
      ...time
    ] as const,
    work: [
      'demo-metrics',
      scope.identity,
      'work-orders',
      scope.session,
      scope.location ?? null,
      scope.descendants,
      ...time
    ] as const,
    history: [
      'demo-metrics',
      scope.identity,
      'history',
      scope.session,
      scope.location ?? null,
      scope.descendants,
      ...time
    ] as const
  };
}

export type DemoAvailability =
  | 'disabled'
  | 'loading'
  | 'denied'
  | 'error'
  | 'unavailable'
  | 'ready';

export function errorStatus(error: unknown): number | undefined {
  const candidate = error as {
    response?: { status?: number };
    status?: number;
  };
  return candidate?.response?.status ?? candidate?.status;
}

export function isDeniedError(error: unknown): boolean {
  const status = errorStatus(error);
  return status === 401 || status === 403;
}

export function demoAvailability(state: {
  enabled: boolean;
  isPending?: boolean;
  isError?: boolean;
  error?: unknown;
  available?: boolean;
}): DemoAvailability {
  if (!state.enabled) return 'disabled';
  if (state.isError) return isDeniedError(state.error) ? 'denied' : 'error';
  if (state.isPending) return 'loading';
  if (state.available === false) return 'unavailable';
  return 'ready';
}

/*
 * On a denied response every cached demo payload is dropped: an actor whose
 * scope shrank must not keep reading earlier results from cache. Only
 * queries that actually hold data are removed — an active errored query
 * would otherwise be re-created and refetched forever. Returns true when
 * the cache was cleared.
 */
export interface RemovableQuery {
  state: { data?: unknown };
}

export function hasCachedData(query: RemovableQuery): boolean {
  return query.state.data !== undefined;
}

export function clearDeniedDemoQueries(
  queryClient: {
    removeQueries: (options: {
      queryKey: readonly unknown[];
      predicate?: (query: RemovableQuery) => boolean;
    }) => void;
  },
  error: unknown
): boolean {
  if (!isDeniedError(error)) return false;
  queryClient.removeQueries({
    queryKey: ['demo-metrics'],
    predicate: hasCachedData
  });
  return true;
}

export interface HistoryChartPoint {
  date: string;
  planned: number | null;
  downtime: number | null;
}

/*
 * Chart mapping: missing coverage stays `null` so the chart shows a gap
 * instead of a zero-loss bar. A zero is only drawn where the backend
 * reported zero loss over observed coverage.
 */
export function historyChartSeries(
  history: Pick<DemoHistory, 'daily'> | undefined | null
): HistoryChartPoint[] {
  return (history?.daily ?? []).map((bucket) => ({
    date: bucket.date,
    planned: bucket.planned_machine_minutes ?? null,
    downtime: bucket.downtime_machine_minutes ?? null
  }));
}

export const COVERAGE_STATES = [
  'fresh',
  'partial',
  'stale',
  'never_seen',
  'not_configured'
] as const;

export const CONDITIONS = ['normal', 'warning', 'critical'] as const;

export function coverageCounts(
  metrics: Pick<DemoMetrics, 'observation_coverage'> | undefined | null
): { state: string; count: number }[] {
  return COVERAGE_STATES.map((state) => ({
    state,
    count: metrics?.observation_coverage?.[state] ?? 0
  }));
}

export function conditionCounts(
  metrics: Pick<DemoMetrics, 'fresh_condition'> | undefined | null
): { condition: string; count: number }[] {
  return CONDITIONS.map((condition) => ({
    condition,
    count: metrics?.fresh_condition?.[condition] ?? 0
  }));
}

export interface DemoHrefScope {
  session?: string | null;
  location?: number | null;
  direct?: boolean;
}

/*
 * Drill-down links keep the demo session and location filters (machine and
 * Maintenance pages receive exactly the active scope); without a session the
 * link carries nothing demo-specific.
 */
export function demoHref(base: string, scope: DemoHrefScope): string {
  const params = new URLSearchParams();
  if (scope.session) params.set('demo_session', scope.session);
  if (scope.location) params.set('location', String(scope.location));
  if (scope.direct) params.set('scope', 'direct');
  const query = params.toString();
  return query ? `${base}?${query}` : base;
}

/*
 * Default history window: whole UTC days, so the request matches the
 * backend's UTC day buckets. Included in query keys verbatim.
 */
export function historyWindow(days: number, now: Date = new Date()) {
  const end = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  );
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
  return { start: start.toISOString(), end: end.toISOString() };
}

export interface DemoUrlScope {
  session: string | null;
  location: number | null;
  direct: boolean;
}

/*
 * Shared URL parsing for every demo-scope destination. The same validation
 * runs wherever the filters arrive (location workspace, maintenance board,
 * machine detail), so a malformed session id is ignored identically and the
 * filters a link carries are exactly the filters a destination applies.
 */
export function parseDemoScope(params: URLSearchParams): DemoUrlScope {
  const raw = params.get('demo_session');
  const session = raw && /^[0-9a-f-]{36}$/i.test(raw) ? raw : null;
  const rawLocation = params.get('location');
  const location =
    rawLocation && /^[0-9]+$/.test(rawLocation) ? Number(rawLocation) : null;
  return { session, location, direct: params.get('scope') === 'direct' };
}

export interface WorkTruncation {
  shown: number;
  total: number;
}

/*
 * The contributing list is bounded server-side while the counts are full
 * totals. Whenever rows were dropped, the UI must say so — a truncated list
 * is never presented as the complete contributing set. Older payloads
 * without the explicit fields fall back to comparing rows against count.
 */
export function workTruncation(
  work: Pick<
    DemoWorkList,
    'count' | 'results' | 'results_returned' | 'has_more'
  >
): WorkTruncation | null {
  const shown = work.results_returned ?? work.results.length;
  if (work.has_more === true || (work.has_more == null && shown < work.count)) {
    return { shown, total: work.count };
  }
  return null;
}
