import { t } from '@lingui/core/macro';
import { BarChart } from '@mantine/charts';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title
} from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';
import {
  type DemoHistory,
  type DemoMetrics,
  type DemoSessionSummary,
  type DemoUrlScope,
  type DemoWorkList,
  clearDeniedDemoQueries,
  conditionCounts,
  coverageCounts,
  demoAvailability,
  demoHref,
  demoMetricsApi,
  demoQueryKeys,
  historyChartSeries,
  historyWindow,
  isDeniedError,
  parseDemoScope,
  workTruncation
} from './demoMetrics';

/*
 * Label lookups run at render time (not module load) so the active Lingui
 * locale is respected; a missing label falls back to the raw API state.
 */
function coverageLabel(state: string): string {
  const labels: Record<string, string> = {
    fresh: t`Fresh (all signals)`,
    partial: t`Partial (some signals missing)`,
    stale: t`Stale (no fresh signal)`,
    never_seen: t`Waiting for first reading`,
    not_configured: t`Not configured`
  };
  return labels[state] ?? state;
}

function conditionLabel(condition: string): string {
  const labels: Record<string, string> = {
    normal: t`Normal`,
    warning: t`Warning`,
    critical: t`Critical`
  };
  return labels[condition] ?? condition;
}

function demoScopeText(scope: DemoUrlScope): string {
  const locationText =
    scope.location == null ? t`all locations` : t`location ${scope.location}`;
  const depthText = scope.direct
    ? t`direct machines only`
    : t`machines including descendants`;
  return `${locationText} · ${depthText}`;
}

function MetricCard({
  label,
  value
}: {
  label: string;
  value: number | string | null;
}) {
  return (
    <Paper withBorder p='md'>
      <Text size='sm' c='dimmed'>
        {label}
      </Text>
      <Text size='xl' fw={700}>
        {value ?? '—'}
      </Text>
    </Paper>
  );
}

export function DemoMetricsPanel({
  session,
  onSessionChange,
  locationId,
  descendants
}: {
  session: string | null;
  onSessionChange: (session: string | null) => void;
  locationId?: number;
  descendants: boolean;
}) {
  const api = useApi();
  const identity = useUserState((s) => s.authGeneration);
  const queryClient = useQueryClient();
  const scope = useMemo(
    () => ({ identity, session, location: locationId, descendants }),
    [identity, session, locationId, descendants]
  );
  const range = useMemo(() => historyWindow(14), [session]);
  const keys = demoQueryKeys({ ...scope, window: range });

  const sessions = useQuery<{ count: number; results: DemoSessionSummary[] }>({
    queryKey: keys.sessions,
    queryFn: async ({ signal }) =>
      (
        await api.get(`${demoMetricsApi}sessions/`, {
          signal
        })
      ).data,
    retry: false
  });

  const enabled = !!session;
  const metrics = useQuery<DemoMetrics>({
    queryKey: keys.metrics,
    queryFn: async ({ signal }) =>
      (
        await api.get(`${demoMetricsApi}sessions/${session}/metrics/`, {
          signal,
          params: { location: locationId, include_descendants: descendants }
        })
      ).data,
    enabled,
    retry: false
  });
  const work = useQuery<DemoWorkList>({
    queryKey: keys.work,
    queryFn: async ({ signal }) =>
      (
        await api.get(`${demoMetricsApi}sessions/${session}/work-orders/`, {
          signal,
          params: { location: locationId, include_descendants: descendants }
        })
      ).data,
    enabled,
    retry: false
  });
  const history = useQuery<DemoHistory>({
    queryKey: keys.history,
    queryFn: async ({ signal }) =>
      (
        await api.get(`${demoMetricsApi}sessions/${session}/history/`, {
          signal,
          params: {
            location: locationId,
            include_descendants: descendants,
            start: range.start,
            end: range.end
          }
        })
      ).data,
    enabled,
    retry: false
  });

  // React Query v5 has no per-query onError: watch the errors instead and
  // drop every cached demo payload when the scope is denied.
  const deniedSource = metrics.error ?? work.error ?? history.error;
  useEffect(() => {
    if (metrics.isError || work.isError || history.isError) {
      clearDeniedDemoQueries(queryClient, deniedSource);
    }
  }, [
    metrics.isError,
    work.isError,
    history.isError,
    deniedSource,
    queryClient
  ]);

  const sessionOptions = (sessions.data?.results ?? []).map((row) => ({
    value: row.id,
    label: `${row.session_key} (${row.status})`
  }));

  const historyAvailability = demoAvailability({
    enabled,
    isPending: history.isPending,
    isError: history.isError,
    error: history.error,
    available: history.data?.available
  });

  const drilldown = {
    session,
    location: locationId,
    direct: !descendants
  };

  const truncation = work.data ? workTruncation(work.data) : null;

  return (
    <Stack gap='md'>
      <Group justify='space-between'>
        <Group>
          <Select
            aria-label={t`Synthetic demo session`}
            placeholder={t`Live data (no synthetic demo)`}
            data={sessionOptions}
            value={session}
            onChange={(value) => {
              // Explicit opt-in; keys carry the session id, so switching
              // sessions never reuses another session's cached data.
              onSessionChange(value);
            }}
            clearable
            searchable={false}
            w={320}
          />
          {session && (
            <>
              <Badge color='violet'>{t`Synthetic demo`}</Badge>
              {metrics.data && (
                <Text size='sm' c='dimmed'>
                  {metrics.data.mode}
                </Text>
              )}
            </>
          )}
        </Group>
        {session && (
          <Button
            variant='default'
            component={Link}
            to={demoHref('/maintenance/board/', drilldown)}
          >{t`View in Maintenance`}</Button>
        )}
      </Group>
      {sessions.isError && (
        <Alert color='red' title={t`Demo sessions unavailable`}>
          {t`The synthetic demo session list could not be loaded. Live data is unaffected.`}
        </Alert>
      )}
      {!session && (
        <Text size='sm' c='dimmed'>
          {t`Historical performance will appear when validated event and placement history is available. Current placement does not establish past downtime.`}
        </Text>
      )}
      {session && (
        <>
          {metrics.isError && (
            <Alert color='red' title={t`Demo metrics unavailable`}>
              {isDeniedMessage(metrics.error)}
              <Button
                variant='subtle'
                onClick={() => metrics.refetch()}
              >{t`Retry`}</Button>
            </Alert>
          )}
          {metrics.isPending && <Loader size='sm' />}
          {metrics.data && (
            <>
              <Text size='xs' c='dimmed'>
                {t`All figures are synthetic demo data generated for this session.`}
              </Text>
              <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <Paper withBorder p='md'>
                  <Text size='sm' fw={600}>
                    {t`Observation coverage`}
                  </Text>
                  <Stack gap={2}>
                    {coverageCounts(metrics.data).map((row) => (
                      <Group key={row.state} justify='space-between'>
                        <Text size='sm'>{coverageLabel(row.state)}</Text>
                        <Text size='sm' fw={600}>
                          {row.count}
                        </Text>
                      </Group>
                    ))}
                  </Stack>
                </Paper>
                <Paper withBorder p='md'>
                  <Text size='sm' fw={600}>
                    {t`Condition (fresh signals only)`}
                  </Text>
                  <Stack gap={2}>
                    {conditionCounts(metrics.data).map((row) => (
                      <Group key={row.condition} justify='space-between'>
                        <Text size='sm'>{conditionLabel(row.condition)}</Text>
                        <Text size='sm' fw={600}>
                          {row.count}
                        </Text>
                      </Group>
                    ))}
                  </Stack>
                  <Text size='xs' c='dimmed'>
                    {t`Coverage is tracked separately; a machine without fresh, good-quality signals has no condition.`}
                  </Text>
                </Paper>
                <Paper withBorder p='md'>
                  <Text size='sm' fw={600}>
                    {t`Session machines`}
                  </Text>
                  <Text size='xl' fw={700}>
                    {metrics.data.cohort_size}
                  </Text>
                  <Text size='xs' c='dimmed'>
                    {t`In scope for the current filters`}
                  </Text>
                </Paper>
              </SimpleGrid>
              <SimpleGrid cols={{ base: 1, sm: 3 }}>
                <MetricCard
                  label={t`Open work orders`}
                  value={metrics.data.open_work_orders}
                />
                <MetricCard
                  label={t`Machines with open work`}
                  value={metrics.data.machines_with_open_work}
                />
                <MetricCard
                  label={t`Overdue open work orders`}
                  value={metrics.data.overdue_open_work_orders}
                />
              </SimpleGrid>
            </>
          )}
          {work.isError && (
            <Alert color='red' title={t`Contributing work list unavailable`}>
              {isDeniedMessage(work.error)}
            </Alert>
          )}
          {work.isPending && <Loader size='sm' />}
          {work.data && (
            <Paper withBorder p='md'>
              <Group justify='space-between'>
                <Title order={5}>{t`Contributing session work`}</Title>
                <Text size='sm' c='dimmed'>
                  {t`${work.data.open_count} open of ${work.data.count}`}
                </Text>
              </Group>
              {truncation && (
                <Alert
                  color='yellow'
                  title={t`Contributing list truncated`}
                  mt='xs'
                >
                  {t`Showing the first ${truncation.shown} of ${truncation.total} contributing rows. The counts above are full totals for the current filters.`}
                </Alert>
              )}
              {work.data.results.length === 0 && (
                <Text size='sm' c='dimmed' mt='xs'>
                  {t`No contributing session work for the current filters.`}
                </Text>
              )}
              {work.data.results.length > 0 && (
                <Table.ScrollContainer minWidth={520}>
                  <Table striped>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>{t`Reference`}</Table.Th>
                        <Table.Th>{t`Title`}</Table.Th>
                        <Table.Th>{t`Machine`}</Table.Th>
                        <Table.Th>{t`State`}</Table.Th>
                        <Table.Th>{t`Due`}</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {work.data.results.map((row) => (
                        <Table.Tr key={row.id}>
                          <Table.Td>{row.reference ?? '—'}</Table.Td>
                          <Table.Td>{row.title}</Table.Td>
                          <Table.Td>
                            <Anchor
                              component={Link}
                              to={demoHref(
                                `/machines/machine/${row.machine_id}/`,
                                drilldown
                              )}
                            >
                              #{row.machine_id}
                            </Anchor>
                          </Table.Td>
                          <Table.Td>
                            <Badge
                              variant='light'
                              color={row.open ? 'blue' : 'gray'}
                            >
                              {row.lifecycle_status}
                            </Badge>
                          </Table.Td>
                          <Table.Td>
                            {row.due_date ?? '—'}
                            {row.overdue && (
                              <Badge color='red' size='sm' ml={4}>
                                {t`Overdue`}
                              </Badge>
                            )}
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              )}
            </Paper>
          )}
          <Paper withBorder p='md'>
            <Group justify='space-between'>
              <Title order={5}>{t`Scoped history`}</Title>
              {historyAvailability === 'ready' && history.data && (
                <Text size='sm' c='dimmed'>
                  {t`Measured ${history.data.measured_machines} of ${history.data.selected_machines} machines`}
                </Text>
              )}
            </Group>
            {historyAvailability === 'loading' && <Loader size='sm' />}
            {historyAvailability === 'denied' && (
              <Alert color='red' title={t`Not authorized`}>
                {t`Your maintenance scope does not cover this demo session.`}
              </Alert>
            )}
            {historyAvailability === 'error' && (
              <Alert color='red' title={t`History request failed`}>
                {t`The history data could not be loaded. No synthetic fallback is shown.`}
                <Button
                  variant='subtle'
                  onClick={() => history.refetch()}
                >{t`Retry`}</Button>
              </Alert>
            )}
            {historyAvailability === 'unavailable' && (
              <Alert color='gray' title={t`History unavailable`}>
                {t`This session has no imported historical coverage, so no chart is shown.`}
              </Alert>
            )}
            {historyAvailability === 'ready' &&
              history.data &&
              history.data.daily.length === 0 && (
                <Text size='sm' c='dimmed'>
                  {t`No planned coverage falls inside the selected window for the current filters.`}
                </Text>
              )}
            {historyAvailability === 'ready' &&
              history.data &&
              history.data.daily.length > 0 && (
                <Stack gap='xs'>
                  <BarChart
                    h={260}
                    data={historyChartSeries(history.data)}
                    dataKey='date'
                    series={[
                      {
                        name: 'planned',
                        color: 'blue.6',
                        label: t`Planned machine-minutes`
                      },
                      {
                        name: 'downtime',
                        color: 'red.6',
                        label: t`Downtime machine-minutes`
                      }
                    ]}
                    withLegend
                    aria-label={t`Synthetic history chart`}
                  />
                  <Text size='xs' c='dimmed'>
                    {t`Days without observation coverage are gaps (null), not zero downtime.`}
                  </Text>
                  <Group gap='xs'>
                    <Badge variant='outline' color='violet'>
                      {t`Synthetic scenario attribution`}
                    </Badge>
                    <Text size='xs' c='dimmed'>
                      {t`Not placement-at-event: current placement does not establish past location.`}
                    </Text>
                  </Group>
                  <Group gap='md'>
                    <Text size='sm'>
                      {t`Planned`}: {history.data.planned_machine_minutes}
                    </Text>
                    <Text size='sm'>
                      {t`Downtime`}:{' '}
                      {history.data.downtime_machine_minutes ?? '—'}
                    </Text>
                    <Text size='sm'>
                      {t`Measured-cohort availability`}:{' '}
                      {history.data.measured_cohort_availability == null
                        ? '—'
                        : `${(history.data.measured_cohort_availability * 100).toFixed(1)}%`}
                    </Text>
                  </Group>
                </Stack>
              )}
          </Paper>
        </>
      )}
    </Stack>
  );
}

function isDeniedMessage(error: unknown): string {
  // Only 401/403 are scope denials; any other failure keeps the plain
  // error message so a server fault is never reported as missing authority.
  return isDeniedError(error)
    ? t`Your maintenance scope does not cover this demo session.`
    : t`The demo metrics could not be loaded. No fixture fallback is shown.`;
}

/*
 * Minimal query state accepted by the shared banner so destinations can pass
 * whatever useQuery result they hold without importing react-query types.
 */
interface DemoWorkQueryState {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  data?: DemoWorkList;
}

/*
 * Scope banner for drill-down destinations (maintenance board). It states
 * which synthetic session and filters are being applied and, when the
 * contributing list is truncated, that the applied filter is bounded — the
 * destination never implies the displayed rows are the complete set.
 */
export function DemoScopeBanner({
  scope,
  work
}: {
  scope: DemoUrlScope;
  work: DemoWorkQueryState;
}) {
  const truncation = work.data ? workTruncation(work.data) : null;
  if (!scope.session) return null;
  return (
    <Alert color='violet' title={t`Synthetic demo session filter`}>
      <Stack gap={4}>
        <Group gap='xs'>
          <Badge color='violet'>{t`Synthetic demo`}</Badge>
          <Text size='sm'>
            {work.data?.session.session_key ?? scope.session}
          </Text>
          <Text size='sm' c='dimmed'>
            {demoScopeText(scope)}
          </Text>
        </Group>
        {work.isPending && (
          <Text size='sm'>{t`Applying the synthetic demo session filter…`}</Text>
        )}
        {work.isError && (
          <Text size='sm'>
            {isDeniedMessage(work.error)}{' '}
            {t`Board cards stay hidden until the session scope resolves.`}
          </Text>
        )}
        {truncation && (
          <Text size='sm'>
            {t`This filter covers the first ${truncation.shown} of ${truncation.total} contributing orders.`}
          </Text>
        )}
      </Stack>
    </Alert>
  );
}

/*
 * Machine drill-down destination scope: applies the session, location and
 * descendant filters from the URL to the machine's session view (membership,
 * coverage and condition come from the cohort-filtered metrics endpoint).
 * A machine outside the filtered cohort says so explicitly instead of
 * silently ignoring the filters.
 */
export function MachineDemoScopeNotice({ machineId }: { machineId: number }) {
  const [params] = useSearchParams();
  const scope = useMemo(() => parseDemoScope(params), [params]);
  const api = useApi();
  const identity = useUserState((s) => s.authGeneration);
  const queryClient = useQueryClient();
  const range = useMemo(() => historyWindow(14), [scope.session]);
  const keys = demoQueryKeys({
    identity,
    session: scope.session,
    location: scope.location,
    descendants: !scope.direct,
    window: range
  });

  const metrics = useQuery<DemoMetrics>({
    queryKey: keys.metrics,
    queryFn: async ({ signal }) =>
      (
        await api.get(`${demoMetricsApi}sessions/${scope.session}/metrics/`, {
          signal,
          params: {
            location: scope.location ?? undefined,
            include_descendants: !scope.direct
          }
        })
      ).data,
    enabled: !!scope.session,
    retry: false
  });

  useEffect(() => {
    if (metrics.isError) {
      clearDeniedDemoQueries(queryClient, metrics.error);
    }
  }, [metrics.isError, metrics.error, queryClient]);

  if (!scope.session) return null;

  const row = metrics.data?.machines.find(
    (machine) => machine.machine_id === machineId
  );

  return (
    <Paper withBorder p='sm'>
      <Stack gap={4}>
        <Group gap='xs'>
          <Badge color='violet'>{t`Synthetic demo`}</Badge>
          <Text size='sm' fw={600}>
            {metrics.data?.session.session_key ?? scope.session}
          </Text>
          <Text size='sm' c='dimmed'>
            {demoScopeText(scope)}
          </Text>
        </Group>
        {metrics.isPending && (
          <Text size='sm' c='dimmed'>
            {t`Checking synthetic demo session scope…`}
          </Text>
        )}
        {metrics.isError && (
          <Text size='sm'>{isDeniedMessage(metrics.error)}</Text>
        )}
        {metrics.data && row && (
          <Text size='sm'>
            {t`In this session's filtered cohort:`}{' '}
            {coverageLabel(row.coverage_state)}
            {' · '}
            {row.condition
              ? conditionLabel(row.condition)
              : t`no fresh condition`}
          </Text>
        )}
        {metrics.data && !row && (
          <Text size='sm'>
            {t`This machine is not in the synthetic demo session cohort for the current filters.`}
          </Text>
        )}
        <Anchor
          component={Link}
          to={demoHref('/maintenance/board/', {
            session: scope.session,
            location: scope.location,
            direct: scope.direct
          })}
          size='sm'
        >
          {t`View session work in Maintenance`}
        </Anchor>
      </Stack>
    </Paper>
  );
}
