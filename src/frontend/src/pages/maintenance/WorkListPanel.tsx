import { plural, t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Modal,
  Pagination,
  Paper,
  SegmentedControl,
  Select,
  Skeleton,
  Stack,
  Table,
  Text,
  TextInput,
  Title
} from '@mantine/core';
import {
  useDebouncedValue,
  useLocalStorage,
  useMediaQuery
} from '@mantine/hooks';
import { IconAdjustments, IconRefresh, IconSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import dayjs from 'dayjs';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { WorkTableScroll } from '../../components/tables/WorkTableScroll';
import { useWorkAssistantClearance } from '../tasks/workorders/useWorkAssistantClearance';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import type { WorkOrder } from '@lib/types/Tasks';
import { useApi } from '../../contexts/ApiContext';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import classes from '../tasks/workorders/workCompanion.module.css';
import {
  WORK_LIST_PAGE_SIZE,
  type WorkAttentionReason,
  type WorkListScope,
  type WorkListState,
  workAttentionReasons,
  workListParams,
  workListPreferenceKey,
  workListRequest,
  workListState,
  workPersonaScope
} from './workListState';

interface WorkListPage {
  count: number;
  results: WorkOrder[];
  next: string | null;
}

function lifecycleOptions() {
  return [
    { value: 'draft', label: t`Draft` },
    { value: 'planned', label: t`Planned` },
    { value: 'ready', label: t`Ready` },
    { value: 'in_progress', label: t`In progress` },
    { value: 'on_hold', label: t`On hold` },
    { value: 'verifying', label: t`Verifying` },
    { value: 'completed', label: t`Completed` },
    { value: 'canceled', label: t`Canceled` }
  ];
}

function WorkFilterFields({
  state,
  onChange
}: {
  state: WorkListState;
  onChange: (changes: Partial<WorkListState>) => void;
}) {
  return (
    <>
      <Select
        label={t`Priority`}
        placeholder={t`All priorities`}
        data={[
          { value: 'high', label: t`High` },
          { value: 'medium', label: t`Medium` },
          { value: 'low', label: t`Low` }
        ]}
        clearable
        value={state.priority || null}
        onChange={(value) =>
          onChange({ priority: (value ?? '') as WorkListState['priority'] })
        }
        w={{ base: '100%', sm: 140 }}
      />
      <Select
        label={t`Lifecycle`}
        placeholder={t`All lifecycles`}
        data={lifecycleOptions()}
        clearable
        value={state.lifecycle || null}
        onChange={(value) =>
          onChange({ lifecycle: (value ?? '') as WorkListState['lifecycle'] })
        }
        w={{ base: '100%', sm: 160 }}
      />
      <Select
        label={t`Work type`}
        placeholder={t`All types`}
        data={[
          { value: 'corrective', label: t`Corrective` },
          { value: 'preventive', label: t`Preventive` },
          { value: 'inspection', label: t`Inspection` },
          { value: 'calibration', label: t`Calibration` },
          { value: 'other', label: t`Other` }
        ]}
        clearable
        value={state.workType || null}
        onChange={(value) =>
          onChange({ workType: (value ?? '') as WorkListState['workType'] })
        }
        w={{ base: '100%', sm: 160 }}
      />
    </>
  );
}

function attentionLabel(reason: WorkAttentionReason): string {
  switch (reason) {
    case 'high_priority':
      return t`High priority`;
    case 'overdue':
      return t`Overdue`;
    case 'on_hold':
      return t`On hold`;
    case 'no_owner':
      return t`No owner`;
  }
}

/**
 * Why a row is in "Needs attention", from the server's named rule. Shown so
 * the queue explains its order instead of hiding it behind a score.
 */
function AttentionReasons({ value }: Readonly<{ value: unknown }>) {
  const reasons = workAttentionReasons(value);
  if (!reasons.length) return null;
  return (
    <Group
      component='ul'
      gap={4}
      aria-label={t`Why it needs attention`}
      style={{ listStyle: 'none', margin: 0, padding: 0 }}
    >
      {reasons.map((reason) => (
        <Badge
          key={reason}
          component='li'
          size='xs'
          variant='outline'
          color='orange'
        >
          {attentionLabel(reason)}
        </Badge>
      ))}
    </Group>
  );
}

function ScopedWorkList({
  actorId,
  identity,
  host
}: { actorId: number; identity: number; host: string }) {
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const phone = useMediaQuery('(max-width: 48em)');
  const assistantSurface = useWorkAssistantClearance(phone);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [savedScope, saveScope] = useLocalStorage<WorkListScope | null>({
    key: workListPreferenceKey(host, actorId),
    defaultValue: null,
    getInitialValueInEffect: false,
    sync: false,
    serialize: (value) => value ?? '',
    deserialize: (value) =>
      value === 'mine' || value === 'attention' || value === 'all'
        ? value
        : null
  });
  // First use only (no scope in the link, nothing saved): start where the
  // reader's persona starts. The list waits for that answer rather than
  // showing All work and then switching; a failed read is All work.
  const firstUse = !params.has('work_scope') && savedScope === null;
  const persona = useQuery({
    queryKey: ['work-persona', host, actorId, identity],
    enabled: firstUse,
    queryFn: async ({ signal }) =>
      (
        await api.get(apiUrl(ApiEndpoints.kanban_work_order_persona), {
          signal
        })
      ).data,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false
  });
  const choosingScope = firstUse && persona.isPending;
  const state = workListState(
    params,
    savedScope ?? (firstUse ? workPersonaScope(persona.data) : 'all')
  );
  const [debouncedSearch] = useDebouncedValue(state?.search ?? '', 200);
  const searchPending = !!state && state.search !== debouncedSearch;
  const request = state
    ? workListRequest({ ...state, search: debouncedSearch }, actorId)
    : null;
  const query = useQuery<WorkListPage>({
    queryKey: ['work-list', host, actorId, identity, request],
    enabled: !!request && !searchPending && !choosingScope,
    queryFn: async ({ signal }) =>
      (
        await api.get(apiUrl(ApiEndpoints.kanban_work_order_table), {
          signal,
          params: request
        })
      ).data,
    retry: false
  });
  const lastPage = Math.max(
    1,
    Math.ceil((query.data?.count ?? 0) / WORK_LIST_PAGE_SIZE)
  );
  const pageOutOfRange = !!state && !!query.data && state.page > lastPage;
  useEffect(() => {
    if (
      state &&
      !searchPending &&
      query.isSuccess &&
      !query.isFetching &&
      pageOutOfRange
    ) {
      setParams(
        (previous) => workListParams(previous, { ...state, page: lastPage }),
        { replace: true }
      );
    }
  }, [
    state,
    searchPending,
    query.isSuccess,
    query.isFetching,
    pageOutOfRange,
    lastPage,
    setParams
  ]);

  const reset = () =>
    setParams((previous) =>
      workListParams(previous, {
        scope: state?.scope ?? savedScope ?? 'all',
        search: '',
        priority: '',
        lifecycle: '',
        workType: '',
        page: 1
      })
    );
  if (!state) {
    return (
      <Alert color='red' title={t`Work view unavailable`}>
        <Stack gap='xs'>
          <Text>{t`This link contains an unsupported work scope or filter.`}</Text>
          <Button
            variant='light'
            w='fit-content'
            onClick={reset}
          >{t`Clear filters`}</Button>
        </Stack>
      </Alert>
    );
  }
  const update = (changes: Partial<WorkListState>, replace = false) =>
    setParams(
      (previous) => {
        const next = workListParams(previous, {
          ...state,
          ...changes,
          page: changes.page ?? 1
        });
        // A search typed before the starting view is known does not pick
        // All work for the reader.
        if (choosingScope && !changes.scope) next.delete('work_scope');
        return next;
      },
      { replace }
    );
  const lifecycles = lifecycleOptions();
  const priorities = [
    { value: 'high', label: t`High` },
    { value: 'medium', label: t`Medium` },
    { value: 'low', label: t`Low` }
  ];
  const visible =
    !query.isError && !searchPending && !pageOutOfRange && query.data;
  const denied =
    isAxiosError(query.error) &&
    [401, 403].includes(query.error.response?.status ?? 0);
  const loading =
    query.isPending || searchPending || (pageOutOfRange && !query.isError);
  const activeFilters = [
    state.priority,
    state.lifecycle,
    state.workType
  ].filter(Boolean).length;
  return (
    <Stack
      ref={assistantSurface}
      component='main'
      aria-labelledby='work-list-title'
      className={classes.surface}
      gap='md'
    >
      <Group justify='space-between' align='flex-start'>
        <Stack gap={4}>
          <Title id='work-list-title' order={1} size='h3'>{t`Work list`}</Title>
          <Text
            size='sm'
            c='dimmed'
          >{t`Review work orders, ownership, and scheduled windows.`}</Text>
        </Stack>
        <Button
          variant='default'
          leftSection={<IconRefresh size={16} />}
          onClick={() => void query.refetch()}
          disabled={searchPending || choosingScope}
        >{t`Refresh work`}</Button>
      </Group>
      <Group justify='space-between'>
        <SegmentedControl
          aria-label={t`Work scope`}
          // Three scopes do not fit side by side on a phone; stack them
          // rather than clip a label.
          orientation={phone ? 'vertical' : 'horizontal'}
          fullWidth={phone}
          // Nothing is selected until the starting view is known.
          disabled={choosingScope}
          value={choosingScope ? '' : state.scope}
          data={[
            { value: 'mine', label: t`My work` },
            { value: 'attention', label: t`Needs attention` },
            { value: 'all', label: t`All work` }
          ]}
          onChange={(value) => {
            const scope = value as WorkListScope;
            saveScope(scope);
            update({ scope });
          }}
        />
        {visible && (
          <Text size='sm' c='dimmed' component='output'>
            {plural(visible.count, {
              one: '# work order',
              other: '# work orders'
            })}{' '}
            ·{' '}
            {state.scope === 'mine'
              ? t`Assigned to you`
              : state.scope === 'attention'
                ? t`High priority, overdue, on hold or without an owner · highest priority first, then earliest due`
                : t`Within your authorized scope`}
          </Text>
        )}
      </Group>
      <Paper withBorder p='sm'>
        <Group align='flex-end' gap='sm'>
          <TextInput
            label={t`Search work`}
            placeholder={t`Reference, title, machine, or owner`}
            leftSection={<IconSearch size={16} />}
            value={state.search}
            onChange={(event) =>
              update({ search: event.currentTarget.value }, true)
            }
            style={{ flex: '2 1 240px' }}
          />
          {phone ? (
            <Button
              variant='default'
              leftSection={<IconAdjustments size={16} />}
              onClick={() => setFiltersOpen(true)}
            >
              {t`Filters`}
              {activeFilters ? ` (${activeFilters})` : ''}
            </Button>
          ) : (
            <WorkFilterFields state={state} onChange={update} />
          )}
        </Group>
      </Paper>
      {phone && (
        <Modal
          opened={filtersOpen}
          onClose={() => setFiltersOpen(false)}
          title={t`Work filters`}
          closeButtonProps={{ 'aria-label': t`Close Work filters` }}
        >
          <Stack gap='md'>
            <WorkFilterFields state={state} onChange={update} />
            <Button
              onClick={() => setFiltersOpen(false)}
            >{t`Done filters`}</Button>
          </Stack>
        </Modal>
      )}
      {query.isFetching &&
        query.data &&
        !query.isError &&
        !searchPending &&
        !pageOutOfRange && (
          <Text
            component='output'
            size='sm'
            c='dimmed'
          >{t`Updating work — showing the last loaded results.`}</Text>
        )}
      {loading && (
        <Stack aria-label={t`Loading work orders`} gap='sm'>
          {[1, 2, 3, 4].map((row) => (
            <Skeleton key={row} height={64} />
          ))}
        </Stack>
      )}
      {query.isError && !searchPending && (
        <Alert
          color='red'
          title={
            denied ? t`Work access unavailable` : t`Work could not be loaded`
          }
        >
          <Stack gap='xs'>
            <Text>
              {denied
                ? t`Your work access could not be confirmed. No cached work is shown.`
                : t`The work source is unavailable. Try again; no cached work is shown.`}
            </Text>
            <Button
              variant='light'
              w='fit-content'
              onClick={() => void query.refetch()}
            >{t`Retry work`}</Button>
          </Stack>
        </Alert>
      )}
      {visible &&
        (visible.results.length ? (
          <>
            <Text size='xs'>{t`Schedule times are shown in your local timezone. Assignment does not indicate acceptance.`}</Text>
            <WorkTableScroll
              minWidth={900}
              label={t`Work orders table, scroll for all columns`}
            >
              <Table
                highlightOnHover
                horizontalSpacing='md'
                verticalSpacing='sm'
                style={{ tableLayout: 'fixed' }}
              >
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th w={112}>{t`Priority`}</Table.Th>
                    <Table.Th w='42%'>{t`Work · machine`}</Table.Th>
                    <Table.Th w={150}>{t`Lifecycle`}</Table.Th>
                    <Table.Th>{t`Owner · window`}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {visible.results.map((work) => (
                    <Table.Tr key={work.id}>
                      <Table.Td style={{ verticalAlign: 'top' }}>
                        <Badge
                          variant='light'
                          color={
                            work.priority === 'high'
                              ? 'red'
                              : work.priority === 'medium'
                                ? 'orange'
                                : 'gray'
                          }
                        >
                          {priorities.find(
                            (option) => option.value === work.priority
                          )?.label ?? work.priority}
                        </Badge>
                      </Table.Td>
                      <Table.Td
                        style={{
                          overflowWrap: 'anywhere',
                          verticalAlign: 'top'
                        }}
                      >
                        <Stack gap={4}>
                          <Anchor
                            component={Link}
                            to={`/maintenance/work-orders/${work.id}/`}
                            fw={600}
                          >
                            {work.reference ?? t`Work order ${work.id}`}
                          </Anchor>
                          <Text size='sm' fw={500} title={work.title}>
                            {work.title}
                          </Text>
                          <Text size='sm' c='dimmed'>
                            {work.machine_name ??
                              (work.machine
                                ? t`Machine ${work.machine}`
                                : t`No machine assigned`)}
                          </Text>
                        </Stack>
                      </Table.Td>
                      <Table.Td style={{ verticalAlign: 'top' }}>
                        <Stack gap={4}>
                          <Text size='sm'>
                            {lifecycles.find(
                              (option) => option.value === work.lifecycle_status
                            )?.label ?? work.lifecycle_status}
                          </Text>
                          <AttentionReasons value={work.attention_reasons} />
                        </Stack>
                      </Table.Td>
                      <Table.Td
                        style={{
                          overflowWrap: 'anywhere',
                          verticalAlign: 'top'
                        }}
                      >
                        <Stack gap={4}>
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
                          <Text size='sm' c='dimmed'>
                            {work.scheduled_start
                              ? dayjs(work.scheduled_start).format(
                                  'D MMM YYYY HH:mm'
                                )
                              : t`Not scheduled`}
                            {work.scheduled_end
                              ? ` — ${dayjs(work.scheduled_end).format('D MMM YYYY HH:mm')}`
                              : ''}
                          </Text>
                          {work.due_date && (
                            <Text size='xs' c='dimmed'>
                              {t`Due`}:{' '}
                              {dayjs(work.due_date).format('D MMM YYYY')}
                            </Text>
                          )}
                        </Stack>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </WorkTableScroll>
            {lastPage > 1 && (
              <Group justify='flex-end'>
                <nav aria-label={t`Work results pagination`}>
                  <Pagination
                    getControlProps={(control) => ({
                      'aria-label': {
                        first: t`First page`,
                        previous: t`Previous page`,
                        next: t`Next page`,
                        last: t`Last page`
                      }[control]
                    })}
                    value={state.page}
                    total={lastPage}
                    onChange={(page) => update({ page })}
                  />
                </nav>
              </Group>
            )}
          </>
        ) : (
          <Paper withBorder p='lg'>
            <Stack gap='sm'>
              <Title order={2} size='h4'>{t`No work in this view`}</Title>
              <Text size='sm' c='dimmed'>
                {state.scope === 'mine'
                  ? t`No assigned work matches this view.`
                  : state.scope === 'attention'
                    ? t`No open work is high priority, overdue, on hold or without an owner in this view.`
                    : t`No authorized work matches these filters.`}
              </Text>
              <Group>
                <Button
                  variant='light'
                  onClick={reset}
                >{t`Clear filters`}</Button>
                {state.scope !== 'all' && (
                  <Button
                    variant='subtle'
                    onClick={() => {
                      saveScope('all');
                      update({ scope: 'all' });
                    }}
                  >{t`Show all work`}</Button>
                )}
              </Group>
            </Stack>
          </Paper>
        ))}
    </Stack>
  );
}

export default function WorkListPanel() {
  const user = useUserState();
  const host = useLocalState((state) => state.hostKey);
  const actorId = user.user?.pk;
  if (!actorId || !user.hasViewRole(UserRoles.work_order))
    return (
      <Alert
        color='red'
        title={t`Work access unavailable`}
      >{t`Your role does not allow viewing work orders.`}</Alert>
    );
  return (
    <ScopedWorkList
      key={JSON.stringify([host, actorId, user.authGeneration])}
      host={host}
      actorId={actorId}
      identity={user.authGeneration}
    />
  );
}
