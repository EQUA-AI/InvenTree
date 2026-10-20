import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Breadcrumbs,
  Button,
  Checkbox,
  Divider,
  Grid,
  Group,
  Loader,
  Pagination,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  useMantineTheme
} from '@mantine/core';
import { useDebouncedValue, useMediaQuery } from '@mantine/hooks';
import { IconMapPin, IconPlus, IconSearch } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { MachineAlarmBadges } from '../../../components/items/MachineAlarmBadges';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';
import { HealthStateBadge } from '../health/common';
import operations from '../operations.module.css';
import { MachinePageSummary } from './MachinePageSummary';

import { machineOpenWorkHref } from '../maintenance/machineWorkViews';
import { LocationBrowser, locationKindLabel } from './LocationBrowser';
import { LocationEditDialog, MachineMoveDialog } from './LocationDialogs';
import {
  type MachineSourceView,
  countSummary,
  locationKind,
  machineDetailHref,
  machineEmptyKind,
  machineLocationId,
  workspaceLabel
} from './locationTree';
import {
  type LocatedMachine,
  type LocationContext,
  type LocationNode,
  type PageResult,
  locationApi,
  locationPath
} from './locationTypes';
import classes from './locations.module.css';
import { type MachineCoverage, machineCondition } from './machineCondition';
import {
  MACHINE_CRITICALITIES,
  MACHINE_PAGE_SIZE,
  type MachineCriticality,
  type MachineListState,
  machineCriticality,
  machineListParams,
  machineListState,
  machineLocationParams
} from './machineListState';
import { type WorkPriority, machineWorkSummary } from './machineWork';

const ROOT_PAGE_SIZE = 10;

export function ScopedMachineList({
  location,
  includeDescendants = true,
  unassigned = false,
  machineId,
  canChange,
  state,
  onStateChange,
  source
}: {
  location?: number;
  includeDescendants?: boolean;
  unassigned?: boolean;
  machineId?: number;
  canChange: boolean;

  state: MachineListState;
  onStateChange: (
    updates: Partial<MachineListState>,
    replace?: boolean
  ) => void;
  source: MachineSourceView;
}) {
  const api = useApi();
  const identity = useUserState((s) => s.authGeneration);
  const { search, alarmsOnly, criticality, page } = state;
  const [debounced] = useDebouncedValue(search, 200);
  const searchPending = search !== debounced;
  const [selection, setSelection] = useState<number[]>([]);
  const [moving, setMoving] = useState<LocatedMachine[] | null>(null);
  useEffect(() => {
    setSelection([]);
  }, [
    search,
    location,
    includeDescendants,
    unassigned,
    machineId,
    alarmsOnly,
    criticality,
    page,
    identity
  ]);

  const query = useQuery<PageResult<LocatedMachine>>({
    queryKey: [
      'asset-locations',
      identity,
      'machines',
      location,
      includeDescendants,
      unassigned,
      machineId,
      alarmsOnly,
      criticality,
      debounced,
      page
    ],
    enabled: !searchPending,
    queryFn: async ({ signal }) =>
      (
        await api.get(`${locationApi}machines/`, {
          signal,
          params: {
            location,
            include_descendants: includeDescendants,
            unassigned,
            machine: machineId,
            has_alarms: alarmsOnly || undefined,
            criticality: criticality || undefined,
            search: debounced,
            limit: MACHINE_PAGE_SIZE,
            offset: (page - 1) * MACHINE_PAGE_SIZE
          }
        })
      ).data
  });
  const lastPage = Math.max(
    1,
    Math.ceil((query.data?.count ?? 0) / MACHINE_PAGE_SIZE)
  );
  const pageOutOfRange = !!query.data && page > lastPage;
  useEffect(() => {
    if (
      !searchPending &&
      query.isSuccess &&
      !query.isFetching &&
      pageOutOfRange
    ) {
      onStateChange({ page: lastPage }, true);
    }
  }, [
    searchPending,
    query.isSuccess,
    query.isFetching,
    pageOutOfRange,
    lastPage,
    onStateChange
  ]);
  const selected = (
    query.isError || searchPending ? [] : (query.data?.results ?? [])
  ).filter((m) => selection.includes(m.pk));
  const allSelected =
    !!query.data?.results.length &&
    selected.length === query.data.results.length;
  const rowScope = {
    location: location ?? null,
    direct: !includeDescendants,
    source,
    list: state
  };
  const placementHref = (locationPk: number) => {
    const linkParams = new URLSearchParams({ location: String(locationPk) });

    if (!includeDescendants) linkParams.set('scope', 'direct');
    return `/machines/index/sites/?${linkParams.toString()}`;
  };
  const emptyKind = machineEmptyKind({
    searched: !!debounced.trim(),
    alarmsOnly,
    filtered: !!criticality,
    unassigned,
    locationSelected: location != null,
    includeDescendants
  });
  return (
    <Stack>
      {query.data && !query.isError && !searchPending && !pageOutOfRange && (
        <MachinePageSummary
          rows={query.data.results}
          total={query.data.count}
          updatedAt={query.dataUpdatedAt}
          updating={query.isFetching}
        />
      )}

      <Group justify='space-between'>
        <Group>
          <TextInput
            aria-label={t`Search machines`}
            placeholder={t`Search machines`}
            description={t`Search this list by name, serial, or manufacturer.`}
            leftSection={<IconSearch size={16} />}
            value={search}
            onChange={(event) =>
              onStateChange({ search: event.currentTarget.value }, true)
            }
          />
          <Switch
            label={t`In alarm only`}
            checked={alarmsOnly}
            onChange={(event) =>
              onStateChange({ alarmsOnly: event.currentTarget.checked })
            }
          />
          <Select
            aria-label={t`Criticality`}
            placeholder={t`Any criticality`}
            clearable
            // The chosen value names its filter; the options stay short.
            data={MACHINE_CRITICALITIES.map((level) => ({
              value: level,
              label: `${t`Criticality`}: ${criticalityLabel(level)}`
            }))}
            renderOption={({ option }) =>
              criticalityLabel(option.value as MachineCriticality)
            }
            value={criticality || null}
            onChange={(value) =>
              onStateChange({ criticality: machineCriticality(value) ?? '' })
            }
            w={190}
          />
        </Group>
        <Group>
          <Text size='sm' c='dimmed'>
            {query.data && !query.isError && !searchPending
              ? t`${query.data.count} machines`
              : ''}
          </Text>
          {selection.length > 0 && (
            <Text size='sm' c='dimmed'>
              {t`Selected on this page`} ({selection.length})
            </Text>
          )}
          {canChange && (
            <Button
              variant='light'
              leftSection={<IconMapPin size={16} />}
              disabled={!selected.length || query.isFetching || query.isError}
              onClick={() => setMoving(selected)}
            >
              {t`Move selected`}
              {selected.length ? ` (${selected.length})` : ''}
            </Button>
          )}
        </Group>
      </Group>
      {(query.isPending ||
        searchPending ||
        (pageOutOfRange && !query.isError)) && <Loader size='sm' />}
      {query.isFetching &&
        query.data &&
        !query.isError &&
        !searchPending &&
        !pageOutOfRange && (
          <Text
            component='output'
            size='sm'
            c='dimmed'
          >{t`Updating machines — showing the last loaded results.`}</Text>
        )}
      {query.isError && !searchPending && (
        <Alert color='red' title={t`Machines unavailable`}>
          {t`Check your maintenance scope or try refreshing.`}
          <Button
            variant='subtle'
            onClick={() => query.refetch()}
          >{t`Retry`}</Button>
        </Alert>
      )}
      {query.data && !query.isError && !searchPending && !pageOutOfRange && (
        <>
          <Text
            size='xs'
            c='dimmed'
          >{t`Scroll the table for location and asset details.`}</Text>
          {/* Below this the table scrolls in place (phones); a 1280px
              desktop still shows every column without scrolling. */}
          <Table.ScrollContainer minWidth={1000}>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  {canChange && (
                    <Table.Th>
                      <Checkbox
                        aria-label={t`Select machines on this page`}
                        checked={allSelected}
                        indeterminate={selected.length > 0 && !allSelected}
                        onChange={(event) =>
                          setSelection(
                            event.currentTarget.checked
                              ? query.data.results.map((m) => m.pk)
                              : []
                          )
                        }
                      />
                    </Table.Th>
                  )}
                  <Table.Th>{t`Machine`}</Table.Th>
                  {/* Short status labels must read whole on a phone; the
                      table scrolls sideways rather than truncating them. */}
                  <Table.Th style={{ minWidth: STATUS_COLUMN_WIDTH }}>
                    {t`Condition`}
                  </Table.Th>
                  <Table.Th style={{ minWidth: STATUS_COLUMN_WIDTH }}>
                    {t`Alarms`}
                  </Table.Th>
                  <Table.Th style={{ minWidth: STATUS_COLUMN_WIDTH }}>
                    {t`Work`}
                  </Table.Th>
                  <Table.Th>{t`Criticality`}</Table.Th>
                  <Table.Th>{t`Physical location`}</Table.Th>
                  <Table.Th>{t`Manufacturer / model`}</Table.Th>
                  <Table.Th>{t`Active record`}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {query.data.results.map((machine) => (
                  <Table.Tr key={machine.pk}>
                    {canChange && (
                      <Table.Td>
                        <Checkbox
                          aria-label={t`Select ${machine.name}`}
                          checked={selection.includes(machine.pk)}
                          onChange={(event) =>
                            setSelection(
                              event.currentTarget.checked
                                ? [...selection, machine.pk]
                                : selection.filter((id) => id !== machine.pk)
                            )
                          }
                        />
                      </Table.Td>
                    )}
                    <Table.Td>
                      <Anchor
                        component={Link}
                        to={machineDetailHref(machine.pk, rowScope)}
                      >
                        {machine.name}
                      </Anchor>
                      {machine.serial && (
                        <Text size='xs' c='dimmed'>
                          {machine.serial}
                        </Text>
                      )}
                    </Table.Td>
                    <MachineConditionCell value={machine.condition} />
                    <Table.Td data-alarms={machine.open_alarms ?? undefined}>
                      {typeof machine.open_alarms !== 'number' ||
                      !Number.isSafeInteger(machine.open_alarms) ||
                      machine.open_alarms < 0 ? (
                        <Text size='sm' c='dimmed'>{t`Unavailable`}</Text>
                      ) : machine.open_alarms > 0 ? (
                        <MachineAlarmBadges
                          open={machine.open_alarms}
                          critical={machine.open_critical_alarms}
                        />
                      ) : (
                        '—'
                      )}
                    </Table.Td>
                    <MachineWorkCell
                      value={machine.open_work}
                      href={machineDetailHref(machine.pk, rowScope)}
                    />
                    <MachineCriticalityCell value={machine.criticality} />
                    <Table.Td>
                      {machine.physical_location ? (
                        <Anchor
                          component={Link}
                          to={placementHref(machine.physical_location.pk)}
                        >
                          {locationPath(machine.physical_location)}
                        </Anchor>
                      ) : (
                        <Badge
                          variant='light'
                          color='gray'
                        >{t`Unassigned`}</Badge>
                      )}
                      {machine.location && (
                        <Text size='xs' c='dimmed'>
                          {t`Legacy label`}: {machine.location}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {[machine.manufacturer, machine.model]
                        .filter(Boolean)
                        .join(' / ') || '—'}
                    </Table.Td>
                    <Table.Td>{machine.active ? t`Yes` : t`No`}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {!query.data.results.length && (
            <Paper withBorder p='lg'>
              {emptyKind === 'no-search-matches' && (
                <Stack gap='xs'>
                  <Text c='dimmed'>{t`No machines match your search.`}</Text>
                  <Button
                    variant='light'
                    size='xs'
                    w='fit-content'
                    onClick={() => onStateChange({ search: '' }, true)}
                  >{t`Clear search`}</Button>
                </Stack>
              )}
              {emptyKind === 'no-filter-matches' && (
                <Stack gap='xs'>
                  <Text c='dimmed'>{t`No machines match these filters.`}</Text>
                  <Button
                    variant='light'
                    size='xs'
                    w='fit-content'
                    onClick={() =>
                      onStateChange({ alarmsOnly: false, criticality: '' })
                    }
                  >{t`Clear filters`}</Button>
                </Stack>
              )}
              {emptyKind === 'no-machines-in-alarm' && (
                <Stack gap='xs'>
                  <Text c='dimmed'>
                    {t`No machine in this list has an active alarm.`}
                  </Text>
                  <Button
                    variant='light'
                    size='xs'
                    w='fit-content'
                    onClick={() => onStateChange({ alarmsOnly: false })}
                  >{t`Show all machines`}</Button>
                </Stack>
              )}
              {emptyKind === 'no-direct-machines' && (
                <Stack gap='xs'>
                  <Text c='dimmed'>
                    {t`No machines are placed directly at this location.`}
                  </Text>
                  <Text size='sm' c='dimmed'>
                    {t`Turn on Include sublocations to see machines placed in sublocations.`}
                  </Text>
                </Stack>
              )}
              {emptyKind === 'no-machines-here' && (
                <Text c='dimmed'>
                  {t`No machines at this location or its sublocations.`}
                </Text>
              )}
              {emptyKind === 'no-unassigned-machines' && (
                <Text c='dimmed'>
                  {t`No unassigned machines in your workspace.`}
                </Text>
              )}
            </Paper>
          )}
          {query.data.count > MACHINE_PAGE_SIZE && (
            <nav aria-label={t`Machine results pagination`}>
              <Pagination
                total={lastPage}
                value={page}
                onChange={(value) => {
                  onStateChange({ page: value });
                }}
              />
            </nav>
          )}
        </>
      )}
      {moving && (
        <MachineMoveDialog
          machines={moving}
          onClose={() => setMoving(null)}
          onSaved={() => {
            setMoving(null);
            setSelection([]);
          }}
        />
      )}
    </Stack>
  );
}

const STATUS_COLUMN_WIDTH = 132;

// The Work list's priority colours, so one priority reads the same everywhere.
const PRIORITY_COLORS: Record<WorkPriority, string> = {
  high: 'red',
  medium: 'orange',
  low: 'gray'
};

/**
 * R6 UI01: open Work for the row's machine, from the reader's Work scope.
 * Condition (Alarms), Work and placement stay separate facts. An unknown
 * count says so instead of showing a reassuring dash. A count opens the
 * machine's Maintenance tab on its Open work view (P12), which counts the
 * same open set in the same scope, with this list's return state kept.
 */
function MachineWorkCell({
  value,
  href
}: Readonly<{ value: unknown; href: string }>) {
  const summary = machineWorkSummary(value);
  if (summary.kind === 'unavailable') {
    return (
      <Table.Td data-open-work='unavailable'>
        <Text size='sm' c='dimmed' title={t`Open work could not be checked`}>
          {t`Not available`}
        </Text>
      </Table.Td>
    );
  }
  if (summary.kind === 'none') {
    return (
      <Table.Td data-open-work='0' aria-label={t`No open work`}>
        —
      </Table.Td>
    );
  }
  const priority = summary.priority;
  return (
    <Table.Td data-open-work={summary.count}>
      <Group gap={6} wrap='nowrap'>
        <Anchor
          component={Link}
          to={machineOpenWorkHref(href)}
          size='sm'
          style={{ whiteSpace: 'nowrap' }}
          aria-label={t`${summary.count} open work orders — open the machine's open work`}
        >
          {t`${summary.count} open`}
        </Anchor>
        {priority && (
          <Badge
            size='xs'
            variant='light'
            color={PRIORITY_COLORS[priority]}
            style={{ flexShrink: 0, overflow: 'visible' }}
          >
            {priority === 'high'
              ? t`High`
              : priority === 'medium'
                ? t`Medium`
                : t`Low`}
          </Badge>
        )}
      </Group>
    </Table.Td>
  );
}

function coverageNote(coverage: MachineCoverage): string | null {
  switch (coverage.kind) {
    case 'unmapped':
      return t`No signals mapped`;
    case 'no-current-data':
      return t`No current data`;
    case 'stale':
      return t`${coverage.stale} of ${coverage.signals} signals stale`;
    case 'poor-quality':
      return t`Poor-quality readings`;
    case 'full':
      return null;
  }
}

/**
 * R6 UI01: the machine's Health condition, judged exactly as its Health tab
 * judges it and drawn with the same badge. A limited assessment says how,
 * on its own line, so coverage is never mistaken for the condition itself.
 */
function MachineConditionCell({ value }: Readonly<{ value: unknown }>) {
  const condition = machineCondition(value);
  if (condition.kind === 'unavailable') {
    return (
      <Table.Td data-condition='unavailable'>
        <Text size='sm' c='dimmed' title={t`Condition could not be checked`}>
          {t`Not available`}
        </Text>
      </Table.Td>
    );
  }
  const note = coverageNote(condition.coverage);
  return (
    <Table.Td data-condition={condition.state}>
      <Stack gap={2} align='flex-start'>
        <HealthStateBadge state={condition.state} size='sm' />
        {note && (
          <Text size='xs' c='dimmed' style={{ whiteSpace: 'nowrap' }}>
            {note}
          </Text>
        )}
      </Stack>
    </Table.Td>
  );
}

function criticalityLabel(level: MachineCriticality): string {
  switch (level) {
    case 'critical':
      return t`Critical`;
    case 'high':
      return t`High`;
    case 'medium':
      return t`Medium`;
    case 'low':
      return t`Low`;
  }
}

/**
 * R6 UI01: the machine's declared criticality, shown as plain text so it is
 * not read as an alarm severity or a work priority. Nothing declared says so
 * rather than defaulting to a level.
 */
function MachineCriticalityCell({ value }: Readonly<{ value: unknown }>) {
  const level = machineCriticality(value);
  return (
    <Table.Td data-criticality={level ?? 'undeclared'}>
      <Text
        size='sm'
        c={level ? undefined : 'dimmed'}
        style={{ whiteSpace: 'nowrap' }}
      >
        {level ? criticalityLabel(level) : t`Not declared`}
      </Text>
    </Table.Td>
  );
}

/** Paginated root-location overview for the unselected main pane (M1). */
function RootOverview({
  identity,
  workspaces
}: {
  identity: number;
  workspaces: { pk: number; name: string }[];
}) {
  const api = useApi();
  const [params] = useSearchParams();
  const [rootPage, setRootPage] = useState(1);
  const rootQuery = useQuery<PageResult<LocationNode>>({
    queryKey: ['asset-locations', identity, 'root-overview', rootPage],
    queryFn: async ({ signal }) =>
      (
        await api.get(locationApi, {
          signal,
          params: {
            parent: 'root',
            limit: ROOT_PAGE_SIZE,
            offset: (rootPage - 1) * ROOT_PAGE_SIZE
          }
        })
      ).data
  });
  const locationHref = (pk: number) => {
    const next = machineLocationParams(params, pk);
    return `?${next.toString()}`;
  };
  return (
    <Paper withBorder p='xl'>
      <Stack>
        <Title order={4}>{t`Browse top-level locations`}</Title>
        <Text c='dimmed'>
          {t`Select a top-level location to see its machines and current maintenance counts.`}
        </Text>
        {rootQuery.isPending && <Loader size='sm' />}
        {rootQuery.isError && (
          <Alert color='red' title={t`Locations unavailable`}>
            {t`Top-level locations could not be loaded.`}
            <Button
              variant='light'
              size='xs'
              onClick={() => rootQuery.refetch()}
            >{t`Retry`}</Button>
          </Alert>
        )}
        {rootQuery.data?.results.map((node) => (
          <Group key={node.pk} justify='space-between' gap='xs'>
            <Anchor component={Link} to={locationHref(node.pk)}>
              {node.name}
            </Anchor>
            <Group gap='xs'>
              <Badge variant='light'>
                {locationKindLabel(locationKind(node.kind))}
              </Badge>
              <Text size='xs' c='dimmed'>
                {node.code}
              </Text>
              {node.archived && (
                <Badge size='xs' color='gray' variant='light'>
                  {t`Archived`}
                </Badge>
              )}
              {workspaces.length > 1 && (
                <Text size='xs' c='dimmed'>
                  {workspaceLabel(node.client, workspaces)}
                </Text>
              )}
            </Group>
          </Group>
        ))}
        {(rootQuery.data?.count ?? 0) > ROOT_PAGE_SIZE && (
          <nav aria-label={t`Location overview pagination`}>
            <Pagination
              total={Math.ceil((rootQuery.data?.count ?? 0) / ROOT_PAGE_SIZE)}
              value={rootPage}
              onChange={setRootPage}
              size='sm'
            />
          </nav>
        )}
        <Button
          component={Link}
          to='/machines/index/unassigned/'
          variant='light'
          w='fit-content'
        >{t`View unassigned machines`}</Button>
      </Stack>
    </Paper>
  );
}

export function LocationWorkspace({
  view = 'sites'
}: { view?: 'sites' | 'all' | 'unassigned' }) {
  const api = useApi();
  const identity = useUserState((s) => s.authGeneration);
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const rawLocation = params.get('location');
  const locationId = machineLocationId(params) ?? undefined;
  const direct = params.get('scope') === 'direct';
  const listState = machineListState(params);
  const updateListState = (
    updates: Partial<MachineListState>,
    replace = false
  ) =>
    setParams((previous) => machineListParams(previous, updates), { replace });

  const [editing, setEditing] = useState<{
    node?: LocationNode;
    parent?: LocationNode;
  } | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const theme = useMantineTheme();
  const wide = useMediaQuery(`(min-width: ${theme.breakpoints.lg})`);

  const context = useQuery<LocationContext>({
    queryKey: ['asset-locations', identity, 'context'],
    queryFn: async ({ signal }) =>
      (await api.get(`${locationApi}context/`, { signal })).data
  });
  const detail = useQuery<LocationNode>({
    queryKey: ['asset-locations', identity, 'detail', locationId],
    queryFn: async ({ signal }) =>
      (
        await api.get(`${locationApi}${locationId}/`, {
          signal
        })
      ).data,
    enabled: view === 'sites' && !!locationId
  });
  const selected = detail.isError ? undefined : detail.data;
  const select = (id: number | null) =>
    setParams((previous) => machineLocationParams(previous, id));
  const counts = selected?.counts;
  const summary = countSummary(counts, {
    direct,
    searched: !!listState.search.trim(),
    alarmsOnly: listState.alarmsOnly,
    filtered: !!listState.criticality
  });
  const countOrders = direct
    ? counts?.direct_open_work_orders
    : counts?.total_open_work_orders;
  const workspaces = context.data?.workspaces ?? [];
  return (
    <Stack>
      <Group justify='space-between'>
        <Box>
          <Title order={3}>
            {view === 'sites'
              ? t`By location`
              : view === 'all'
                ? t`All Machines`
                : t`Unassigned machines`}
          </Title>
          {(view !== 'sites' || !selected) && (
            <Text size='sm' c='dimmed'>
              {view === 'sites'
                ? t`Organize physical locations and see where maintenance is needed.`
                : t`Physical placement is separate from the legacy location label.`}
            </Text>
          )}
        </Box>
        <Group>
          {view === 'sites' && context.data && !context.isError && (
            <Button
              style={{ display: wide ? 'none' : undefined }}
              variant='default'
              leftSection={<IconMapPin size={16} />}
              aria-expanded={mobileOpen}
              aria-controls='machine-location-browser'
              onClick={() => setMobileOpen((open) => !open)}
            >
              {t`Choose location`}
            </Button>
          )}

          <Button
            variant='default'
            onClick={() =>
              queryClient.invalidateQueries({ queryKey: ['asset-locations'] })
            }
          >{t`Refresh`}</Button>
          {view === 'sites' && !context.isError && context.data?.can_add && (
            <Button
              leftSection={<IconPlus size={16} />}
              onClick={() => setEditing({})}
            >{t`Add location`}</Button>
          )}
        </Group>
      </Group>
      {(view !== 'sites' || !selected) && (
        <Text size='xs' c='dimmed'>{t`Plant timezone: Unavailable`}</Text>
      )}
      {context.isPending && <Loader size='sm' />}
      {context.isError && (
        <Alert
          color='red'
          title={t`Location workspace unavailable`}
        >{t`Your role or maintenance scope could not be resolved. Refresh or ask your administrator to check access.`}</Alert>
      )}
      {context.data &&
        !context.isError &&
        (view !== 'sites' ? (
          <ScopedMachineList
            key={view}
            unassigned={view === 'unassigned'}
            canChange={context.data.can_change}
            state={listState}
            onStateChange={updateListState}
            source={view === 'all' ? 'machines' : 'unassigned'}
          />
        ) : (
          <>
            <Grid>
              <Grid.Col
                id='machine-location-browser'
                className={classes.browser}
                data-mobile-open={mobileOpen ? 'true' : 'false'}
                data-wide={wide ? 'true' : 'false'}
                span={{ base: 12, lg: 3 }}
              >
                <LocationBrowser
                  selected={selected}
                  onSelect={select}
                  identity={identity}
                  workspaces={workspaces}
                />
              </Grid.Col>
              <Grid.Col span={{ base: 12, lg: 9 }}>
                <Stack>
                  {rawLocation && !locationId && (
                    <Alert color='red'>{t`The location link is invalid.`}</Alert>
                  )}
                  {detail.isFetching && locationId && <Loader size='sm' />}
                  {detail.isError && (
                    <Alert color='red'>{t`This location is unavailable or you no longer have access.`}</Alert>
                  )}
                  {selected && !detail.isError ? (
                    <>
                      <nav aria-label={t`Location path`}>
                        <Breadcrumbs>
                          {selected.path.map((node) => (
                            <Anchor
                              key={node.pk}
                              component={Link}
                              to={`?${machineLocationParams(params, node.pk)}`}
                              aria-current={
                                node.pk === selected.pk ? 'page' : undefined
                              }
                            >
                              {node.name}
                            </Anchor>
                          ))}
                        </Breadcrumbs>
                      </nav>
                      <Paper withBorder p='sm' radius='sm'>
                        <Stack gap={6}>
                          <Group justify='space-between'>
                            <Group>
                              <Title order={3}>{selected.name}</Title>
                              <Badge variant='light'>
                                {locationKindLabel(locationKind(selected.kind))}
                              </Badge>
                              <Badge variant='outline'>{selected.code}</Badge>
                              {selected.archived && (
                                <Badge color='gray'>{t`Archived`}</Badge>
                              )}
                            </Group>
                            <Group>
                              {context.data.can_add && !selected.archived && (
                                <Button
                                  variant='light'
                                  size='xs'
                                  onClick={() =>
                                    setEditing({ parent: selected })
                                  }
                                >{t`Add sublocation`}</Button>
                              )}
                              {context.data.can_change && (
                                <Button
                                  variant='default'
                                  size='xs'
                                  onClick={() => setEditing({ node: selected })}
                                >{t`Edit location`}</Button>
                              )}
                            </Group>
                          </Group>
                          {selected.description && (
                            <Text size='sm'>{selected.description}</Text>
                          )}
                          <Text size='xs' c='dimmed'>
                            {t`Location timezone`}:{' '}
                            {selected.effective_timezone ?? t`Not configured`} ·{' '}
                            {t`Plant timezone: Unavailable`}
                          </Text>
                          <Group gap='sm'>
                            <Switch
                              label={t`Include sublocations`}
                              checked={!direct}
                              onChange={(event) =>
                                setParams((previous) => {
                                  const next = machineListParams(previous, {
                                    page: 1
                                  });
                                  if (event.currentTarget.checked)
                                    next.delete('scope');
                                  else next.set('scope', 'direct');
                                  return next;
                                })
                              }
                            />
                            <Text size='xs' c='dimmed'>
                              {direct
                                ? t`Includes only machines placed directly at this location.`
                                : t`Includes machines placed at this location and all of its sublocations.`}
                            </Text>
                          </Group>
                          <Divider />
                          <div className={operations.metrics}>
                            <div className={operations.metric}>
                              <Text
                                className={operations.label}
                              >{t`Machines in this view`}</Text>
                              <Text
                                className={operations.value}
                                data-testid='machine-count-view'
                              >
                                {summary.view ?? t`Unavailable`}
                              </Text>
                              <Text size='xs' c='dimmed'>
                                {summary.showDirectBreakdown
                                  ? t`Including sublocations`
                                  : t`Direct here`}
                              </Text>
                            </div>
                            {summary.showDirectBreakdown && (
                              <div className={operations.metric}>
                                <Text
                                  className={operations.label}
                                >{t`Direct at this location`}</Text>
                                <Text
                                  className={operations.value}
                                  data-testid='machine-count-direct'
                                >
                                  {summary.direct ?? t`Unavailable`}
                                </Text>
                              </div>
                            )}
                            <div className={operations.metric}>
                              <Text
                                className={operations.label}
                              >{t`Open work orders now`}</Text>
                              <Text className={operations.value}>
                                {countOrders ?? t`Unavailable`}
                              </Text>
                              <Text
                                size='xs'
                                c='dimmed'
                              >{t`Drafts excluded; work orders counted once.`}</Text>
                            </div>
                          </div>
                        </Stack>
                      </Paper>
                      {summary.locationTotalsOnly && (
                        <Text
                          size='xs'
                          c='dimmed'
                          data-testid='location-totals-note'
                        >
                          {t`Location totals — machine search and alarm filters do not change these counts.`}
                        </Text>
                      )}
                      <ScopedMachineList
                        location={selected.pk}
                        includeDescendants={!direct}
                        canChange={context.data.can_change}
                        state={listState}
                        onStateChange={updateListState}
                        source='sites'
                      />
                    </>
                  ) : (
                    !locationId && (
                      <RootOverview
                        identity={identity}
                        workspaces={workspaces}
                      />
                    )
                  )}
                </Stack>
              </Grid.Col>
            </Grid>
          </>
        ))}
      {editing && context.data && !context.isError && (
        <LocationEditDialog
          {...editing}
          context={context.data}
          onClose={() => setEditing(null)}
          onSaved={(node) => {
            setEditing(null);
            select(node.pk);
          }}
        />
      )}
    </Stack>
  );
}
