import { plural, t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Pagination,
  Paper,
  SegmentedControl,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  TextInput,
  Title
} from '@mantine/core';
import { useDebouncedValue, useMediaQuery } from '@mantine/hooks';
import { IconRefresh, IconSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import dayjs from 'dayjs';
import { type ReactNode, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { WorkOrder } from '@lib/types/Tasks';
import { useApi } from '../../../contexts/ApiContext';
import { MaintenanceRecordTable } from '../../../tables/assets/MaintenanceRecordTable';
import {
  type MachineWorkCounts,
  type MachineWorkState,
  OPEN_WORK_PAGE_SIZE,
  machineWorkCounts,
  machineWorkParams,
  machineWorkState,
  openCount,
  openWorkRequest
} from './machineWorkViews';

interface WorkPage {
  count: number;
  results: WorkOrder[];
}

const TOP = { verticalAlign: 'top' } as const;
const WRAP = { verticalAlign: 'top', overflowWrap: 'anywhere' } as const;

const PRIORITY_COLORS: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'gray'
};

function priorityLabel(priority: string): string {
  switch (priority) {
    case 'high':
      return t`High`;
    case 'medium':
      return t`Medium`;
    case 'low':
      return t`Low`;
    default:
      return priority;
  }
}

function lifecycleLabel(status: string): string {
  switch (status) {
    case 'draft':
      return t`Draft`;
    case 'planned':
      return t`Planned`;
    case 'ready':
      return t`Ready`;
    case 'in_progress':
      return t`In progress`;
    case 'on_hold':
      return t`On hold`;
    case 'verifying':
      return t`Verifying`;
    case 'completed':
      return t`Completed`;
    case 'canceled':
      return t`Canceled`;
    default:
      return status;
  }
}

/** A scheduled window; a same-day end shows its time only. */
export function windowText(start: string | null, end: string | null) {
  if (!start) return null;
  const from = dayjs(start);
  if (!end) return from.format('D MMM YYYY HH:mm');
  const to = dayjs(end);
  return from.isSame(to, 'day')
    ? `${from.format('D MMM YYYY HH:mm')}–${to.format('HH:mm')}`
    : `${from.format('D MMM YYYY HH:mm')} — ${to.format('D MMM YYYY HH:mm')}`;
}

function WorkIdentity({ work }: Readonly<{ work: WorkOrder }>) {
  return (
    <Stack gap={2}>
      <Anchor
        component={Link}
        to={`/maintenance/work-orders/${work.id}/`}
        fw={600}
        size='sm'
      >
        {work.reference ?? t`Work order ${work.id}`}
      </Anchor>
      <Text size='sm' lineClamp={2} title={work.title}>
        {work.title}
      </Text>
    </Stack>
  );
}

function WorkComponent({ work }: Readonly<{ work: WorkOrder }>) {
  return work.affected_component ? (
    <Stack gap={2}>
      <Text size='sm'>{work.affected_component}</Text>
      {work.affected_component_ref && (
        <Text size='xs' c='dimmed'>
          {work.affected_component_ref}
        </Text>
      )}
    </Stack>
  ) : (
    <Text size='sm' c='dimmed'>
      {t`Not recorded`}
    </Text>
  );
}

function WorkOwner({ work }: Readonly<{ work: WorkOrder }>) {
  return (
    <>
      <Text size='sm'>
        {work.assigned_to
          ? work.assigned_to_name ||
            work.assigned_to_username ||
            t`User ${work.assigned_to}`
          : t`Unassigned`}
      </Text>
      {!work.assigned_to && work.assignee && (
        <Text size='xs' c='dimmed'>
          {t`Legacy assignee`}: {work.assignee}
        </Text>
      )}
    </>
  );
}

function WorkWindow({ work }: Readonly<{ work: WorkOrder }>) {
  return (
    <>
      <Text size='sm'>
        {windowText(work.scheduled_start, work.scheduled_end) ??
          t`Not scheduled`}
      </Text>
      {work.due_date && (
        <Text size='xs' c='dimmed'>
          {t`Due`} {dayjs(work.due_date).format('D MMM YYYY')}
        </Text>
      )}
    </>
  );
}

function PriorityBadge({ priority }: Readonly<{ priority: string }>) {
  return (
    <Badge variant='light' color={PRIORITY_COLORS[priority] ?? 'gray'}>
      {priorityLabel(priority)}
    </Badge>
  );
}

function CardField({
  label,
  children
}: Readonly<{ label: string; children: ReactNode }>) {
  return (
    <Stack gap={0} style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
      <Text size='xs' c='dimmed' fw={600}>
        {label}
      </Text>
      {children}
    </Stack>
  );
}

/** "Open work" or "Completed history" with its count, or why it has none. */
function ViewLabel({
  label,
  count,
  loading
}: Readonly<{ label: string; count: number | null; loading: boolean }>) {
  return (
    <Group gap={6} wrap='nowrap' justify='center'>
      <span>{label}</span>
      {loading ? (
        <Skeleton height={16} width={22} radius='xl' />
      ) : count === null ? (
        <Text span size='xs' c='dimmed'>{t`not available`}</Text>
      ) : (
        <Badge size='sm' variant='light' color='gray' circle={count < 10}>
          {count}
        </Badge>
      )}
    </Group>
  );
}

/**
 * The machine's open work, read from the native paginated Work list with the
 * same open set and scope the counts use. A row opens its owning work order;
 * nothing here changes lifecycle or completes work.
 */
function OpenWork({
  machineId,
  state,
  counts,
  countsLoaded,
  onChange,
  onShowCompleted,
  onRefreshCounts
}: Readonly<{
  machineId: number;
  state: MachineWorkState;
  counts: MachineWorkCounts;
  countsLoaded: boolean;
  onChange: (changes: Partial<MachineWorkState>, replace?: boolean) => void;
  onShowCompleted: () => void;
  onRefreshCounts: () => void;
}>) {
  const api = useApi();
  const phone = useMediaQuery('(max-width: 48em)');
  const [search] = useDebouncedValue(state.search, 200);
  const searchPending = search !== state.search;
  const request = openWorkRequest(machineId, { search, page: state.page });
  const scopeKnown = counts.open.kind !== 'unavailable';
  const query = useQuery<WorkPage>({
    queryKey: ['machine-open-work', machineId, request],
    enabled: countsLoaded && scopeKnown && !searchPending,
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get(apiUrl(ApiEndpoints.kanban_work_order_table), {
          signal,
          params: request
        })
      ).data
  });
  const lastPage = Math.max(
    1,
    Math.ceil((query.data?.count ?? 0) / OPEN_WORK_PAGE_SIZE)
  );
  const outOfRange = !!query.data && state.page > lastPage;
  useEffect(() => {
    if (query.isSuccess && !query.isFetching && outOfRange)
      onChange({ page: lastPage }, true);
  }, [query.isSuccess, query.isFetching, outOfRange, lastPage]);

  if (!countsLoaded) return <Skeleton height={120} />;
  if (!scopeKnown) {
    return (
      <Alert color='gray' title={t`Open work could not be checked`}>
        {t`Your work scope could not be confirmed, so open work on this machine is not listed. This is not the same as having no open work.`}
      </Alert>
    );
  }
  const denied =
    isAxiosError(query.error) &&
    [401, 403].includes(query.error.response?.status ?? 0);
  const expected = openCount(counts.open);
  const rows = !query.isError && !searchPending && !outOfRange && query.data;
  // The tab's count and this list come from two reads; say so if they differ
  // rather than letting one silently contradict the other.
  const disagree =
    rows && !search.trim() && expected !== null && rows.count !== expected;

  return (
    <Stack gap='sm'>
      <Group align='flex-end' gap='sm' wrap='wrap'>
        <TextInput
          label={t`Search open work`}
          placeholder={t`Reference, title or owner`}
          leftSection={<IconSearch size={16} />}
          value={state.search}
          onChange={(event) =>
            onChange({ search: event.currentTarget.value, page: 1 }, true)
          }
          style={{ flex: '1 1 260px' }}
        />
        {rows && (
          <Text size='sm' c='dimmed' component='output'>
            {plural(rows.count, {
              one: '# open work order',
              other: '# open work orders'
            })}
            {' · '}
            {t`newest first`}
          </Text>
        )}
      </Group>
      {disagree && (
        <Alert color='yellow' title={t`Counts changed while loading`}>
          <Group justify='space-between' gap='xs'>
            <Text size='sm'>{t`The open count and this list were read moments apart and no longer agree.`}</Text>
            <Button
              size='xs'
              variant='light'
              onClick={() => {
                onRefreshCounts();
                void query.refetch();
              }}
            >{t`Refresh both`}</Button>
          </Group>
        </Alert>
      )}
      {query.isFetching && query.data && !query.isError && !searchPending && (
        <Text component='output' size='sm' c='dimmed'>
          {t`Updating open work — showing the last loaded results.`}
        </Text>
      )}
      {(query.isPending || searchPending || (outOfRange && !query.isError)) &&
        !query.isError && (
          <Stack aria-label={t`Loading open work`} gap='xs'>
            {[1, 2, 3].map((row) => (
              <Skeleton key={row} height={56} />
            ))}
          </Stack>
        )}
      {query.isError && !searchPending && (
        <Alert
          color='red'
          title={
            denied
              ? t`Work access unavailable`
              : t`Open work could not be loaded`
          }
        >
          <Stack gap='xs'>
            <Text size='sm'>
              {denied
                ? t`Your work access could not be confirmed. No cached work is shown.`
                : t`The work source is unavailable. Try again; no cached work is shown.`}
            </Text>
            <Button
              variant='light'
              w='fit-content'
              onClick={() => void query.refetch()}
            >{t`Retry open work`}</Button>
          </Stack>
        </Alert>
      )}
      {rows &&
        (rows.results.length ? (
          <>
            <Text size='xs' c='dimmed'>
              {t`Open means Planned, Ready, In progress, On hold or Verifying. Windows are in your local timezone; assignment does not indicate acceptance.`}
            </Text>
            {phone ? (
              // Phones reorganize the same fields into one card per job,
              // so lifecycle and owner are not hidden behind a side scroll.
              <Stack gap='xs' data-testid='machine-open-work'>
                {rows.results.map((work) => (
                  <Paper key={work.id} withBorder p='sm' data-work={work.id}>
                    <Stack gap='xs'>
                      <Group justify='space-between' gap='xs' wrap='nowrap'>
                        <PriorityBadge priority={work.priority} />
                        <Text size='sm'>
                          {lifecycleLabel(work.lifecycle_status)}
                        </Text>
                      </Group>
                      <WorkIdentity work={work} />
                      <SimpleGrid cols={2} spacing='xs'>
                        <CardField label={t`Component`}>
                          <WorkComponent work={work} />
                        </CardField>
                        <CardField label={t`Owner`}>
                          <WorkOwner work={work} />
                        </CardField>
                      </SimpleGrid>
                      <CardField label={t`Window`}>
                        <WorkWindow work={work} />
                      </CardField>
                    </Stack>
                  </Paper>
                ))}
              </Stack>
            ) : (
              <Table.ScrollContainer minWidth={860}>
                <Table
                  data-testid='machine-open-work'
                  highlightOnHover
                  verticalSpacing='sm'
                  style={{ tableLayout: 'fixed' }}
                >
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th w={96}>{t`Priority`}</Table.Th>
                      <Table.Th w='32%'>{t`Work`}</Table.Th>
                      <Table.Th>{t`Component`}</Table.Th>
                      <Table.Th w={120}>{t`Lifecycle`}</Table.Th>
                      <Table.Th>{t`Owner`}</Table.Th>
                      <Table.Th>{t`Window`}</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {rows.results.map((work) => (
                      <Table.Tr key={work.id} data-work={work.id}>
                        <Table.Td style={TOP}>
                          <PriorityBadge priority={work.priority} />
                        </Table.Td>
                        <Table.Td style={WRAP}>
                          <WorkIdentity work={work} />
                        </Table.Td>
                        <Table.Td style={WRAP}>
                          <WorkComponent work={work} />
                        </Table.Td>
                        <Table.Td style={TOP}>
                          <Text size='sm'>
                            {lifecycleLabel(work.lifecycle_status)}
                          </Text>
                        </Table.Td>
                        <Table.Td style={WRAP}>
                          <WorkOwner work={work} />
                        </Table.Td>
                        <Table.Td style={TOP}>
                          <WorkWindow work={work} />
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
            {lastPage > 1 && (
              <Group justify='flex-end'>
                <nav aria-label={t`Open work pagination`}>
                  <Pagination
                    value={state.page}
                    total={lastPage}
                    onChange={(page) => onChange({ page })}
                  />
                </nav>
              </Group>
            )}
          </>
        ) : (
          <Paper withBorder p='lg'>
            <Stack gap='xs'>
              <Title order={5}>
                {search.trim()
                  ? t`No open work matches this search`
                  : t`No open work on this machine`}
              </Title>
              <Text size='sm' c='dimmed'>
                {search.trim()
                  ? t`Other open work on this machine may not match the search.`
                  : t`Nothing planned, ready, in progress, on hold or verifying in your authorized scope.`}
              </Text>
              <Group gap='xs'>
                {search.trim() ? (
                  <Button
                    variant='light'
                    onClick={() => onChange({ search: '', page: 1 })}
                  >{t`Clear search`}</Button>
                ) : (
                  counts.completed.kind === 'known' &&
                  counts.completed.count > 0 && (
                    <Button variant='light' onClick={onShowCompleted}>
                      {t`Show completed history`}
                    </Button>
                  )
                )}
              </Group>
            </Stack>
          </Paper>
        ))}
    </Stack>
  );
}

/**
 * R6 UI06 Machine Maintenance: Open work and Completed history as two
 * separate views of this machine's work, each with its own count from the
 * same scope. Completed history is the native maintenance-record table.
 */
export function MachineMaintenancePanel({
  machineId
}: Readonly<{ machineId: number }>) {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const phone = useMediaQuery('(max-width: 48em)');
  const state = machineWorkState(params);
  const summary = useQuery({
    queryKey: ['machine-work-summary', machineId],
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get(
          apiUrl(ApiEndpoints.asset_machine_work_summary, machineId),
          { signal }
        )
      ).data as unknown
  });
  const counts = machineWorkCounts(summary.data);
  const loading = summary.isPending;
  const update = (changes: Partial<MachineWorkState>, replace = false) =>
    setParams(
      (previous) =>
        machineWorkParams(previous, {
          ...(state ?? { view: 'open', search: '', page: 1 }),
          ...changes
        }),
      { replace }
    );

  if (!state) {
    return (
      <Alert
        color='red'
        title={t`Maintenance view unavailable`}
        data-testid='machine-maintenance'
      >
        <Stack gap='xs'>
          <Text size='sm'>{t`This link names a maintenance view or page that does not exist.`}</Text>
          <Button
            variant='light'
            w='fit-content'
            onClick={() =>
              setParams(
                (previous) =>
                  machineWorkParams(previous, {
                    view: 'open',
                    search: '',
                    page: 1
                  }),
                { replace: true }
              )
            }
          >{t`Show open work`}</Button>
        </Stack>
      </Alert>
    );
  }

  const open = openCount(counts.open);
  const completed =
    counts.completed.kind === 'known' ? counts.completed.count : null;

  return (
    <Stack gap='sm' data-testid='machine-maintenance'>
      {/* On a phone the two views stack full width, each label whole. */}
      <Group
        justify='space-between'
        align={phone ? 'stretch' : 'center'}
        gap='sm'
        style={phone ? { flexDirection: 'column' } : undefined}
      >
        <SegmentedControl
          aria-label={t`Maintenance view`}
          orientation={phone ? 'vertical' : 'horizontal'}
          fullWidth={phone}
          value={state.view}
          onChange={(value) =>
            update({ view: value as MachineWorkState['view'], page: 1 })
          }
          data={[
            {
              value: 'open',
              label: (
                <ViewLabel
                  label={t`Open work`}
                  count={open}
                  loading={loading}
                />
              )
            },
            {
              value: 'completed',
              label: (
                <ViewLabel
                  label={t`Completed history`}
                  count={completed}
                  loading={loading}
                />
              )
            }
          ]}
        />
        <Button
          variant='default'
          size='xs'
          w={phone ? 'fit-content' : undefined}
          leftSection={<IconRefresh size={14} />}
          loading={summary.isFetching}
          onClick={() => void summary.refetch()}
        >{t`Refresh counts`}</Button>
      </Group>
      {summary.isError && (
        <Alert color='red' title={t`Maintenance counts could not be loaded`}>
          <Group justify='space-between' gap='xs'>
            <Text size='sm'>{t`Counts are not shown rather than shown as zero.`}</Text>
            <Button
              size='xs'
              variant='light'
              onClick={() => void summary.refetch()}
            >{t`Retry counts`}</Button>
          </Group>
        </Alert>
      )}
      {!!open && !!completed && (
        <Text size='sm' c='dimmed'>
          {t`Each job keeps its own outcome: a completed job does not close the open work, and open work does not undo a recorded completion.`}
        </Text>
      )}
      {state.view === 'open' ? (
        <OpenWork
          machineId={machineId}
          state={state}
          counts={counts}
          countsLoaded={summary.isSuccess}
          onChange={update}
          onShowCompleted={() => update({ view: 'completed', page: 1 })}
          onRefreshCounts={() => void summary.refetch()}
        />
      ) : (
        <Stack gap='xs'>
          <Text size='xs' c='dimmed'>
            {t`Completed maintenance records for this machine. A record opens the work order that completed it; legacy records have none.`}
          </Text>
          <MaintenanceRecordTable machineId={machineId} boundToMachine />
        </Stack>
      )}
    </Stack>
  );
}
