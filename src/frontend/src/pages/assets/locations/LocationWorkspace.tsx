import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Box,
  Breadcrumbs,
  Button,
  Checkbox,
  Grid,
  Group,
  Loader,
  Pagination,
  Paper,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconMapPin, IconPlus, IconSearch } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';

import { LocationBrowser, locationKindLabel } from './LocationBrowser';
import { LocationEditDialog, MachineMoveDialog } from './LocationDialogs';
import {
  type MachineSourceView,
  countSummary,
  locationKind,
  machineDetailHref,
  machineEmptyKind,
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

const ROOT_PAGE_SIZE = 10;

export function ScopedMachineList({
  location,
  includeDescendants = true,
  unassigned = false,
  machineId,
  canChange,
  search,
  onSearchChange,
  source
}: {
  location?: number;
  includeDescendants?: boolean;
  unassigned?: boolean;
  machineId?: number;
  canChange: boolean;

  search: string;
  onSearchChange: (value: string) => void;
  source: MachineSourceView;
}) {
  const api = useApi();
  const identity = useUserState((s) => s.authGeneration);
  const [debounced] = useDebouncedValue(search, 200);
  const [page, setPage] = useState(1);
  const [selection, setSelection] = useState<number[]>([]);
  const [moving, setMoving] = useState<LocatedMachine[] | null>(null);

  // A different machine population (location, scope, search) must
  // never keep stale paging or row selection (plan M3/U2).
  useEffect(() => {
    setPage(1);
    setSelection([]);
  }, [search, location, includeDescendants, unassigned, machineId]);

  const query = useQuery<PageResult<LocatedMachine>>({
    queryKey: [
      'asset-locations',
      identity,
      'machines',
      location,
      includeDescendants,
      unassigned,
      machineId,

      debounced,
      page
    ],
    queryFn: async ({ signal }) =>
      (
        await api.get(`${locationApi}machines/`, {
          signal,
          params: {
            location,
            include_descendants: includeDescendants,
            unassigned,
            machine: machineId,

            search: debounced,
            limit: 25,
            offset: (page - 1) * 25
          }
        })
      ).data
  });
  const selected = (query.isError ? [] : (query.data?.results ?? [])).filter(
    (m) => selection.includes(m.pk)
  );
  const allSelected =
    !!query.data?.results.length &&
    selected.length === query.data.results.length;
  // Row links hand the physical-location filters and the source view to the
  // machine page, which returns to this exact list (U2).
  const rowScope = {
    location: location ?? null,
    direct: !includeDescendants,
    source
  };
  const placementHref = (locationPk: number) => {
    const linkParams = new URLSearchParams({ location: String(locationPk) });

    if (!includeDescendants) linkParams.set('scope', 'direct');
    return `/machines/index/sites/?${linkParams.toString()}`;
  };
  const emptyKind = machineEmptyKind({
    searched: !!debounced,
    unassigned,
    locationSelected: location != null,
    includeDescendants
  });
  return (
    <Stack>
      <Group justify='space-between'>
        <TextInput
          aria-label={t`Search machines`}
          placeholder={t`Search machines`}
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(event) => onSearchChange(event.currentTarget.value)}
        />
        <Group>
          <Text size='sm' c='dimmed'>
            {query.data ? t`${query.data.count} machines` : ''}
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
      {query.isPending && <Loader size='sm' />}
      {query.isError && (
        <Alert color='red' title={t`Machines unavailable`}>
          {t`Check your maintenance scope or try refreshing.`}
          <Button
            variant='subtle'
            onClick={() => query.refetch()}
          >{t`Retry`}</Button>
        </Alert>
      )}
      {query.data && !query.isError && (
        <>
          <Table.ScrollContainer minWidth={650}>
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
                    onClick={() => onSearchChange('')}
                  >{t`Clear search`}</Button>
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
          {query.data.count > 25 && (
            <nav aria-label={t`Machine results pagination`}>
              <Pagination
                total={Math.ceil(query.data.count / 25)}
                value={page}
                onChange={(value) => {
                  setPage(value);
                  setSelection([]);
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
    const next = new URLSearchParams(params);
    next.set('location', String(pk));
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
  const locationId =
    rawLocation && /^\d+$/.test(rawLocation) && Number(rawLocation) > 0
      ? Number(rawLocation)
      : undefined;
  const direct = params.get('scope') === 'direct';

  const [editing, setEditing] = useState<{
    node?: LocationNode;
    parent?: LocationNode;
  } | null>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [machineSearch, setMachineSearch] = useState('');
  useEffect(() => {
    // A new location/view is a new machine population: start unsearched.
    setMachineSearch('');
  }, [locationId, view]);

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
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (id) next.set('location', String(id));
      else next.delete('location');
      return next;
    });
  const counts = selected?.counts;
  const summary = countSummary(counts, {
    direct,
    searched: !!machineSearch.trim()
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
          <Text size='sm' c='dimmed'>
            {view === 'sites'
              ? t`Organize physical locations and see where maintenance is needed.`
              : t`Physical placement is separate from the legacy location label.`}
          </Text>
        </Box>
        <Group>
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
            search={machineSearch}
            onSearchChange={setMachineSearch}
            source={view === 'all' ? 'machines' : 'unassigned'}
          />
        ) : (
          <>
            <Button
              className={classes.chooseLocation}
              variant='default'
              leftSection={<IconMapPin size={16} />}
              aria-expanded={mobileOpen}
              aria-controls='machine-location-browser'
              onClick={() => setMobileOpen((open) => !open)}
            >
              {t`Choose location`}
            </Button>
            <Grid>
              <Grid.Col
                id='machine-location-browser'
                className={classes.browser}
                data-mobile-open={mobileOpen ? 'true' : 'false'}
                span={{ base: 12, md: 3 }}
              >
                <LocationBrowser
                  selected={selected}
                  onSelect={select}
                  identity={identity}
                  workspaces={workspaces}
                />
              </Grid.Col>
              <Grid.Col span={{ base: 12, md: 9 }}>
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
                              to={`?location=${node.pk}${direct ? '&scope=direct' : ''}`}
                              aria-current={
                                node.pk === selected.pk ? 'page' : undefined
                              }
                            >
                              {node.name}
                            </Anchor>
                          ))}
                        </Breadcrumbs>
                      </nav>
                      <Paper withBorder p='md'>
                        <Stack gap='sm'>
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
                            <Text>{selected.description}</Text>
                          )}
                          <Text size='sm' c='dimmed'>
                            {t`Timezone`}:{' '}
                            {selected.effective_timezone ?? t`Not configured`}
                          </Text>
                          <Switch
                            label={t`Include sublocations`}
                            checked={!direct}
                            onChange={(event) =>
                              setParams((previous) => {
                                const next = new URLSearchParams(previous);
                                if (event.currentTarget.checked)
                                  next.delete('scope');
                                else next.set('scope', 'direct');
                                return next;
                              })
                            }
                          />
                          <Text size='sm' c='dimmed'>
                            {direct
                              ? t`Includes only machines placed directly at this location.`
                              : t`Includes machines placed at this location and all of its sublocations.`}
                          </Text>
                        </Stack>
                      </Paper>
                      <SimpleGrid cols={{ base: 1, sm: 3 }}>
                        <Paper withBorder p='md'>
                          <Text size='sm' c='dimmed'>
                            {t`Machines in this view`}
                          </Text>
                          <Text
                            size='xl'
                            fw={700}
                            data-testid='machine-count-view'
                          >
                            {summary.view ?? '—'}
                          </Text>
                          <Text size='xs' c='dimmed'>
                            {summary.showDirectBreakdown
                              ? t`Including sublocations`
                              : t`Direct here`}
                          </Text>
                        </Paper>
                        {summary.showDirectBreakdown && (
                          <Paper withBorder p='md'>
                            <Text size='sm' c='dimmed'>
                              {t`Direct at this location`}
                            </Text>
                            <Text
                              size='xl'
                              fw={700}
                              data-testid='machine-count-direct'
                            >
                              {summary.direct ?? '—'}
                            </Text>
                            <Text size='xs' c='dimmed'>
                              {t`Direct here`}
                            </Text>
                          </Paper>
                        )}
                        <Paper withBorder p='md'>
                          <Text size='sm' c='dimmed'>
                            {t`Open work orders now`}
                          </Text>
                          <Text size='xl' fw={700}>
                            {countOrders ?? '—'}
                          </Text>
                          <Text size='xs' c='dimmed'>
                            {t`Drafts excluded; work orders counted once.`}
                          </Text>
                        </Paper>
                      </SimpleGrid>
                      {summary.locationTotalsOnly && (
                        <Text
                          size='xs'
                          c='dimmed'
                          data-testid='location-totals-note'
                        >
                          {t`Location totals — the machine table search does not change these counts.`}
                        </Text>
                      )}
                      <ScopedMachineList
                        location={selected.pk}
                        includeDescendants={!direct}
                        canChange={context.data.can_change}
                        search={machineSearch}
                        onSearchChange={setMachineSearch}
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
