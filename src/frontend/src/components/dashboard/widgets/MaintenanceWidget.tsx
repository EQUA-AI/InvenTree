import { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl } from '@lib/functions/Navigation';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Progress,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title
} from '@mantine/core';
import { useDocumentVisibility, useLocalStorage } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { type MouseEvent, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import {
  InvalidReadResponse,
  readFailure,
  readPollInterval,
  readQueryPolicy
} from '../../../functions/readQueryPolicy';
import { useAIChatState } from '../../../states/AIChatState';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import { ReadErrorNotice } from '../../common/ReadErrorNotice';
import { type MaintenanceMetric, metricLabels } from './maintenanceMetrics';

type Filters = {
  period: string;
  from: string;
  to: string;
  horizon: string;
  machine: string;
  client: string;
  assigned_to: string;
  priority: string;
  type: string;
  criticality: string;
  mine: string;
};
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

/** One reusable renderer, one independently configurable library entry per metric. */
export default function MaintenanceWidget({
  definition
}: { definition: MaintenanceMetric }) {
  const user = useUserState();
  const host = useLocalState((state) => state.getHost());
  const generation = useAIChatState((s) => s.sessionGeneration);
  // Remount configuration on identity/permission boundaries, including open dialogs.
  return (
    <MaintenanceWidgetContent
      key={`${host}:${user.user?.pk}:${generation}`}
      definition={definition}
    />
  );
}

function MaintenanceWidgetContent({
  definition
}: { definition: MaintenanceMetric }) {
  const api = useApi();
  const host = useLocalState((state) => state.getHost());
  const user = useUserState();
  const generation = useAIChatState((s) => s.sessionGeneration);
  const visibility = useDocumentVisibility();
  const labels = metricLabels();
  const [filters, setFilters] = useLocalStorage<Filters>({
    key: `maintenance-widget:${user.user?.pk}:${definition.id}`,
    defaultValue: {
      period: definition.id === 'repeat' ? '90' : 'month',
      from: '',
      to: '',
      horizon: '7',
      machine: '',
      client: '',
      assigned_to: '',
      priority: '',
      type: '',
      criticality: '',
      mine: ''
    }
  });
  const [configuring, setConfiguring] = useState(false);
  const [details, setDetails] = useState(false);
  const [group, setGroup] = useState('');
  const [offset, setOffset] = useState(0);
  const permitted = user.hasViewPermission(ModelType.workorder);
  const customValid =
    filters.period !== 'custom' ||
    (!!filters.from && !!filters.to && filters.from <= filters.to);
  const setFilter = (key: keyof Filters, value: string) => {
    setFilters((current) => ({ ...current, [key]: value }));
    setOffset(0);
    setGroup('');
  };
  const query = useQuery<MaintenanceResult>({
    ...readQueryPolicy,
    queryKey: [
      'maintenance-metrics',
      host,
      user.user?.pk,
      generation,
      definition.id,
      filters,
      group,
      offset
    ],
    enabled: permitted && customValid && visibility === 'visible',
    refetchInterval: (query) =>
      query.state.data?.state === 'unavailable'
        ? false
        : readPollInterval(query, 60_000),
    refetchIntervalInBackground: false,
    queryFn: async ({ signal }) => {
      const { data } = await api.get('/api/aichat/ui/maintenance-metrics/', {
        baseURL: host,
        params: { ...filters, metric: definition.id, group, offset },
        signal
      });
      try {
        return parseMaintenanceResult(data, definition.id);
      } catch {
        throw new InvalidReadResponse('Invalid maintenance result');
      }
    }
  });
  const data =
    query.data?.state === 'ready' &&
    permitted &&
    (!query.error || readFailure(query.error) === 'temporary')
      ? query.data
      : undefined;
  const followRecord = (event: MouseEvent<HTMLAnchorElement>) => {
    if (
      event.button === 0 &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.shiftKey &&
      !event.altKey
    ) {
      setDetails(false);
      setGroup('');
      setOffset(0);
    }
  };
  const openDetails = (key = '') => {
    setGroup(key);
    setOffset(0);
    setDetails(true);
  };
  const enums = (values: string[]) =>
    values.map((value) => ({ value, label: labels[value] || value }));
  const unit =
    data?.unit === 'percent'
      ? '%'
      : data?.unit === 'minutes'
        ? t`minutes`
        : data?.unit === 'machines'
          ? t`machines`
          : t`work orders`;
  const dateText = (value?: string) =>
    value
      ? new Date(value).toLocaleString(undefined, { timeZone: data?.timezone })
      : '—';
  return (
    <Stack
      gap='xs'
      style={{ height: '100%', overflowY: 'auto' }}
      data-testid={`maintenance-${definition.id}`}
    >
      <Group justify='space-between' align='start'>
        <Title order={3} size='h4'>
          {definition.title}
        </Title>
        <Button
          variant='subtle'
          size='compact-xs'
          onClick={() => setConfiguring(!configuring)}
          aria-expanded={configuring}
        >{t`Filters`}</Button>
      </Group>
      <Text size='xs' c='dimmed'>
        {definition.description}
      </Text>
      {definition.period && (
        <Select
          aria-label={t`Reporting period`}
          value={filters.period}
          onChange={(v) => setFilter('period', v || 'month')}
          data={[
            { value: 'today', label: t`Today` },
            { value: 'week', label: t`This week` },
            { value: 'month', label: t`This month` },
            { value: '30', label: t`Last 30 days` },
            { value: '90', label: t`Last 90 days` },
            { value: 'custom', label: t`Custom` }
          ]}
          size='xs'
        />
      )}
      {definition.period && filters.period === 'custom' && (
        <Group grow>
          <TextInput
            type='date'
            label={t`From`}
            value={filters.from}
            onChange={(e) => setFilter('from', e.currentTarget.value)}
          />
          <TextInput
            type='date'
            label={t`Through`}
            value={filters.to}
            onChange={(e) => setFilter('to', e.currentTarget.value)}
          />
        </Group>
      )}
      {definition.id === 'preventive' && (
        <Select
          label={t`Upcoming calendar days`}
          value={filters.horizon}
          data={['7', '14', '30']}
          onChange={(v) => setFilter('horizon', v || '7')}
          size='xs'
        />
      )}
      {configuring && (
        <Stack gap='xs'>
          <Select
            label={t`Client`}
            clearable
            searchable
            value={filters.client || null}
            data={data?.clients || []}
            onChange={(v) => setFilter('client', v || '')}
          />
          <Select
            label={t`Machine`}
            searchable
            clearable
            value={filters.machine || null}
            data={data?.machines || []}
            onChange={(v) => setFilter('machine', v || '')}
          />
          <Select
            label={t`Machine criticality`}
            clearable
            value={filters.criticality || null}
            data={enums(['critical', 'high', 'medium', 'low'])}
            onChange={(v) => setFilter('criticality', v || '')}
          />
          {!definition.machine && (
            <>
              <Select
                label={t`Assigned user`}
                clearable
                searchable
                value={filters.assigned_to || null}
                data={data?.assignees || []}
                onChange={(v) => setFilter('assigned_to', v || '')}
              />
              <Select
                label={t`Priority`}
                clearable
                value={filters.priority || null}
                data={enums(['high', 'medium', 'low'])}
                onChange={(v) => setFilter('priority', v || '')}
              />
              <Select
                label={t`Work-order type`}
                clearable
                value={filters.type || null}
                data={enums([
                  'corrective',
                  'preventive',
                  'inspection',
                  'calibration',
                  'other'
                ])}
                onChange={(v) => setFilter('type', v || '')}
              />
              <Checkbox
                label={t`Assigned to me`}
                checked={filters.mine === 'true'}
                onChange={(e) =>
                  setFilter('mine', e.currentTarget.checked ? 'true' : '')
                }
              />
            </>
          )}
          <Button
            size='xs'
            variant='default'
            onClick={() => {
              setFilters({
                ...filters,
                machine: '',
                client: '',
                assigned_to: '',
                priority: '',
                type: '',
                criticality: '',
                mine: ''
              });
              setGroup('');
              setOffset(0);
            }}
          >{t`Reset scope filters`}</Button>
        </Stack>
      )}
      {!permitted ? (
        <Alert color='gray'>{t`Unavailable for your current role or maintenance scope.`}</Alert>
      ) : !customValid ? (
        <Text size='sm'>{t`Choose a valid start and end date.`}</Text>
      ) : query.isPending ? (
        <Loader size='sm' />
      ) : query.isError ? (
        <ReadErrorNotice
          error={query.error}
          stale={!!data}
          retry={() => void query.refetch()}
        />
      ) : !data ? (
        <Alert color='gray'>
          {t`Unavailable for your current role or maintenance scope.`}
          <Button
            size='compact-xs'
            variant='subtle'
            onClick={() => void query.refetch()}
          >{t`Retry`}</Button>
        </Alert>
      ) : null}
      {data && customValid && (
        <>
          <Group justify='space-between'>
            <Text size='xl' fw={700}>
              {data.value === null
                ? t`No qualifying records`
                : `${data.value.toLocaleString()} ${unit}`}
            </Text>
            <Badge variant='light'>
              {definition.period
                ? data.partial_period
                  ? t`Period in progress`
                  : t`Selected period`
                : t`Now`}
            </Badge>
          </Group>
          <Button variant='light' size='xs' onClick={() => openDetails()}>
            {t`View contributing records`} ({data.record_count})
          </Button>
          <Stack gap={4}>
            {data.groups.map((g) => (
              <Button
                key={g.key}
                aria-label={`${labels[g.label] || g.label}: ${g.value}`}
                variant='subtle'
                h='auto'
                p={4}
                onClick={() => openDetails(g.key)}
                styles={{
                  inner: { display: 'block', width: '100%' },
                  label: { display: 'block', width: '100%' }
                }}
              >
                <Group justify='space-between'>
                  <Text size='xs'>{labels[g.label] || g.label}</Text>
                  <Text size='xs'>
                    {g.value.toLocaleString()}
                    {definition.id === 'mix' && data.record_count > 0
                      ? ` (${((100 * g.count) / data.record_count).toFixed(1)}%)`
                      : ''}
                  </Text>
                </Group>
                {definition.id === 'assignment' && (
                  <Text size='xs'>
                    {t`High priority`}: {g.high_priority || 0} · {t`Overdue`}:{' '}
                    {g.overdue || 0}
                  </Text>
                )}
                <Progress
                  aria-label={labels[g.label] || g.label}
                  value={Math.min(
                    100,
                    (100 * g.value) /
                      Math.max(1, ...data.groups.map((v) => v.value))
                  )}
                  size={4}
                />
              </Button>
            ))}
          </Stack>
          {Object.entries(data.missing)
            .filter(([, n]) => n > 0)
            .map(([key, n]) => (
              <Text key={key} size='xs' c='orange'>
                {labels[key] || key}: {n}
              </Text>
            ))}
          {Object.entries(data.stats)
            .filter(([key, value]) => typeof value === 'number' && labels[key])
            .map(([key, value]) => (
              <Text key={key} size='xs'>
                {key === 'drafts' ? (
                  <Button
                    variant='subtle'
                    size='compact-xs'
                    onClick={() => openDetails('_drafts')}
                  >
                    {labels[key]}: {value}
                  </Button>
                ) : (
                  <>
                    {labels[key]}: {value}
                  </>
                )}
              </Text>
            ))}
          {typeof data.stats.comparison_from === 'string' && (
            <Text size='xs' c='dimmed'>
              {t`Comparison interval`}: {dateText(data.stats.comparison_from)} –{' '}
              {dateText(String(data.stats.comparison_to))}
            </Text>
          )}
          {definition.period && (
            <Text size='xs' c='dimmed'>
              {dateText(data.from)} – {dateText(data.to)}
            </Text>
          )}
          <Text size='xs' c='dimmed'>
            {t`Your authorized records`} · {t`As of`}{' '}
            {dateText(data.observed_at)} · {data.timezone} · v{data.version}
          </Text>
        </>
      )}
      <Modal
        opened={details && permitted}
        onClose={() => {
          setDetails(false);
          setOffset(0);
          setGroup('');
        }}
        title={definition.title}
        size='xl'
      >
        <Stack>
          <Text size='sm'>{t`Live records may have changed since the count was loaded.`}</Text>
          {group && (
            <Button
              size='xs'
              variant='subtle'
              onClick={() => {
                setGroup('');
                setOffset(0);
              }}
            >{t`Show all contributing records`}</Button>
          )}
          {query.isFetching ? (
            <Loader size='sm' />
          ) : query.isError || !data ? (
            <Alert>{t`Records unavailable`}</Alert>
          ) : (
            <>
              <Text size='sm'>
                {data.detail_count} {t`records`}
              </Text>
              <Table.ScrollContainer minWidth={520}>
                <Table>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>{t`Record`}</Table.Th>
                      <Table.Th>{t`Machine / status`}</Table.Th>
                      <Table.Th>{t`Recorded details`}</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {data.records.map((r) => (
                      <Table.Tr key={r.pk}>
                        <Table.Td>
                          <Anchor
                            component={Link}
                            onClick={followRecord}
                            to={getDetailUrl(
                              r.model === 'workorder'
                                ? ModelType.workorder
                                : ModelType.assetmachine,
                              r.pk
                            )}
                          >
                            {r.label}
                          </Anchor>
                        </Table.Td>
                        <Table.Td>
                          {r.visible_machine ? (
                            <Anchor
                              component={Link}
                              onClick={followRecord}
                              to={getDetailUrl(
                                ModelType.assetmachine,
                                r.visible_machine
                              )}
                            >
                              {r.machine_label}
                            </Anchor>
                          ) : null}
                          <Text size='xs'>
                            {labels[r.lifecycle_status || ''] || ''}{' '}
                            {labels[r.priority || ''] || ''}
                            {r.assigned_user && (
                              <Text size='xs'>
                                {t`Assigned to`}: {r.assigned_user}
                              </Text>
                            )}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          {r.due_date && (
                            <Text size='xs'>
                              {t`Due`}: {r.due_date}
                            </Text>
                          )}
                          {r.actual_completed_at && (
                            <Text size='xs'>
                              {t`Completed`}: {dateText(r.actual_completed_at)}
                            </Text>
                          )}
                          {definition.id === 'age' && (
                            <Text size='xs'>
                              {t`Created`}: {dateText(r.created_at)}
                            </Text>
                          )}
                          {r.verifying_since && (
                            <Text size='xs'>
                              {t`Verifying since`}:{' '}
                              {dateText(r.verifying_since)}
                            </Text>
                          )}
                          {r.minutes !== undefined && (
                            <Text size='xs'>
                              {r.minutes} {t`minutes`}{' '}
                              {r.amended ? t`Amended` : ''}
                            </Text>
                          )}
                          {r.parts_gap && definition.id === 'holds' && (
                            <Text size='xs'>{t`Parts not fully allocated`}</Text>
                          )}
                          {r.data_health && (
                            <Text size='xs'>
                              {t`Data health`}: {labels[r.data_health]}
                            </Text>
                          )}
                          {r.linked_orders?.map((order) => (
                            <Anchor
                              key={order.pk}
                              component={Link}
                              onClick={followRecord}
                              to={getDetailUrl(ModelType.workorder, order.pk)}
                            >
                              {order.label}{' '}
                            </Anchor>
                          ))}
                          {r.hold_reason && (
                            <Text size='xs'>{r.hold_reason}</Text>
                          )}
                          {r.alert_count !== undefined && (
                            <Text size='xs'>
                              {t`Active alerts`}: {r.alert_count}
                              <br />
                              {t`First observed`}:{' '}
                              {dateText(r.first_observed_at)}
                              <br />
                              {t`Last observed`}: {dateText(r.last_observed_at)}
                            </Text>
                          )}
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
              {data.detail_count === 0 && (
                <Text>{t`No qualifying records`}</Text>
              )}
              <Group justify='space-between'>
                <Button
                  variant='default'
                  disabled={!offset}
                  onClick={() => setOffset(Math.max(0, offset - 25))}
                >{t`Previous`}</Button>
                <Button
                  variant='default'
                  disabled={data.next_offset === null}
                  onClick={() => setOffset(data.next_offset || 0)}
                >{t`Next`}</Button>
              </Group>
            </>
          )}
        </Stack>
      </Modal>
    </Stack>
  );
}
