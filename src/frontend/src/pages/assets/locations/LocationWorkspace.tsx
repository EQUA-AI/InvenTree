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
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tree,
  type TreeNodeData,
  useTree
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import {
  IconBuildingFactory2,
  IconMapPin,
  IconPlus,
  IconSearch
} from '@tabler/icons-react';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';
import { LocationEditDialog, MachineMoveDialog } from './LocationDialogs';
import {
  type LocatedMachine,
  type LocationContext,
  type LocationNode,
  type PageResult,
  locationApi,
  locationPath
} from './locationTypes';

function LocationBrowser({
  selected,
  onSelect,
  identity
}: {
  selected?: LocationNode;
  onSelect: (id: number | null) => void;
  identity: number;
}) {
  const api = useApi();
  const [search, setSearch] = useState('');
  const [debounced] = useDebouncedValue(search, 200);
  const [searchPage, setSearchPage] = useState(1);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (selected)
      setExpanded((previous) => ({
        ...previous,
        ...Object.fromEntries(
          selected.path.map((node) => [String(node.pk), true])
        )
      }));
  }, [selected?.path.map((node) => node.pk).join(',')]);
  const parents = [
    'root',
    ...Object.keys(expanded).filter((id) => expanded[id])
  ];
  const branches = useQueries({
    queries: parents.map((parent) => ({
      queryKey: ['asset-locations', identity, 'branch', parent],
      queryFn: async ({ signal }: { signal: AbortSignal }) =>
        (await api.get(locationApi, { signal, params: { parent, limit: 100 } }))
          .data as PageResult<LocationNode>
    }))
  });
  const searchQuery = useQuery<PageResult<LocationNode>>({
    queryKey: ['asset-locations', identity, 'search', debounced, searchPage],
    queryFn: async ({ signal }) =>
      (
        await api.get(locationApi, {
          signal,
          params: {
            search: debounced,
            limit: 10,
            offset: (searchPage - 1) * 10
          }
        })
      ).data,
    enabled: !!debounced
  });
  const data: TreeNodeData[] = useMemo(() => {
    const byParent = new Map(
      parents.map((parent, i) => [
        parent,
        branches[i].isError ? undefined : branches[i].data
      ])
    );
    const build = (parent: string, depth = 0): TreeNodeData[] => {
      if (depth > 32) return [];
      const branch = byParent.get(parent);
      return (branch?.results ?? []).map((node) => ({
        value: String(node.pk),
        label: node.archived ? `${node.name} (${t`Archived`})` : node.name,
        children: node.has_children
          ? byParent.has(String(node.pk)) && byParent.get(String(node.pk))
            ? build(String(node.pk), depth + 1)
            : [
                {
                  value: `pending-${node.pk}`,
                  label: t`Expand to load sublocations`
                }
              ]
          : undefined
      }));
    };
    return build('root');
  }, [
    branches.map((b) => `${b.dataUpdatedAt}-${b.isError}`).join(','),
    parents.join(',')
  ]);
  const tree = useTree({
    expandedState: expanded,
    onExpandedStateChange: setExpanded,
    selectedState: selected ? [String(selected.pk)] : [],
    onSelectedStateChange: (ids) => {
      if (/^\d+$/.test(ids[0] ?? '')) onSelect(Number(ids[0]));
    }
  });
  return (
    <Paper withBorder p='md'>
      <Stack gap='sm'>
        <Group gap='xs'>
          <IconBuildingFactory2 size={20} />
          <Text fw={600}>{t`Locations`}</Text>
        </Group>
        <TextInput
          aria-label={t`Search locations`}
          placeholder={t`Search names or codes`}
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(event) => {
            setSearch(event.currentTarget.value);
            setSearchPage(1);
          }}
        />
        {debounced ? (
          <>
            {searchQuery.isFetching && <Loader size='sm' />}
            {searchQuery.isError && (
              <Alert color='red'>{t`Locations could not be loaded.`}</Alert>
            )}
            {!searchQuery.isError &&
              searchQuery.data?.results.map((node) => (
                <Anchor
                  key={node.pk}
                  component='button'
                  ta='left'
                  onClick={() => {
                    onSelect(node.pk);
                    setSearch('');
                  }}
                >
                  {locationPath(node)}
                </Anchor>
              ))}
            {searchQuery.data?.count === 0 && (
              <Text c='dimmed' size='sm'>{t`No matching locations`}</Text>
            )}
            {(searchQuery.data?.count ?? 0) > 10 && (
              <Pagination
                total={Math.ceil((searchQuery.data?.count ?? 0) / 10)}
                value={searchPage}
                onChange={setSearchPage}
                size='xs'
              />
            )}
          </>
        ) : (
          <>
            <Anchor
              component='button'
              ta='left'
              onClick={() => onSelect(null)}
            >{t`All top-level locations`}</Anchor>
            {branches[0].isPending && <Loader size='sm' />}
            {branches.some((b) => b.isError) && (
              <Alert color='red'>{t`Some locations could not be loaded.`}</Alert>
            )}
            <Tree
              data={data}
              tree={tree}
              selectOnClick
              aria-label={t`Sites and facilities`}
            />
            {branches.some((b) => !!b.data?.next) && (
              <Text
                size='xs'
                c='dimmed'
              >{t`This branch shows its first 100 locations. Search by name or code to find more.`}</Text>
            )}
            {branches[0].data?.count === 0 && (
              <Text
                c='dimmed'
                size='sm'
              >{t`Create your first site to organize machines.`}</Text>
            )}
          </>
        )}
      </Stack>
    </Paper>
  );
}

export function ScopedMachineList({
  location,
  includeDescendants = true,
  unassigned = false,
  machineId,
  canChange
}: {
  location?: number;
  includeDescendants?: boolean;
  unassigned?: boolean;
  machineId?: number;
  canChange: boolean;
}) {
  const api = useApi();
  const identity = useUserState((s) => s.authGeneration);
  const [search, setSearch] = useState('');
  const [debounced] = useDebouncedValue(search, 200);
  const [page, setPage] = useState(1);
  const [selection, setSelection] = useState<number[]>([]);
  const [moving, setMoving] = useState<LocatedMachine[] | null>(null);
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
  return (
    <Stack>
      <Group justify='space-between'>
        <TextInput
          aria-label={t`Search machines`}
          placeholder={t`Search machines`}
          leftSection={<IconSearch size={16} />}
          value={search}
          onChange={(event) => {
            setSearch(event.currentTarget.value);
            setPage(1);
            setSelection([]);
          }}
        />
        <Group>
          <Text size='sm' c='dimmed'>
            {query.data ? t`${query.data.count} machines` : ''}
          </Text>
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
                  <Table.Th>{t`Active`}</Table.Th>
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
                        to={`/machines/machine/${machine.pk}/`}
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
                          to={`/machines/index/sites/?location=${machine.physical_location.pk}`}
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
              <Text c='dimmed'>
                {unassigned
                  ? t`No unassigned machines in your workspace.`
                  : t`No machines match this view.`}
              </Text>
            </Paper>
          )}
          {query.data.count > 25 && (
            <Pagination
              total={Math.ceil(query.data.count / 25)}
              value={page}
              onChange={(value) => {
                setPage(value);
                setSelection([]);
              }}
            />
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
  const context = useQuery<LocationContext>({
    queryKey: ['asset-locations', identity, 'context'],
    queryFn: async ({ signal }) =>
      (await api.get(`${locationApi}context/`, { signal })).data
  });
  const detail = useQuery<LocationNode>({
    queryKey: ['asset-locations', identity, 'detail', locationId],
    queryFn: async ({ signal }) =>
      (await api.get(`${locationApi}${locationId}/`, { signal })).data,
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
  const countMachines = direct
    ? counts?.direct_machines
    : counts?.total_machines;
  const countOrders = direct
    ? counts?.direct_open_work_orders
    : counts?.total_open_work_orders;
  return (
    <Stack>
      <Group justify='space-between'>
        <Box>
          <Title order={3}>
            {view === 'sites'
              ? t`Sites & Facilities`
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
          />
        ) : (
          <Grid>
            <Grid.Col span={{ base: 12, md: 3 }}>
              <LocationBrowser
                selected={selected}
                onSelect={select}
                identity={identity}
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
                    <Paper withBorder p='md'>
                      <Stack gap='sm'>
                        <Group justify='space-between'>
                          <Group>
                            <Title order={3}>{selected.name}</Title>
                            <Badge variant='light'>{selected.code}</Badge>
                            {selected.archived && (
                              <Badge color='gray'>{t`Archived`}</Badge>
                            )}
                          </Group>
                          <Group>
                            {context.data.can_add && !selected.archived && (
                              <Button
                                variant='light'
                                size='xs'
                                onClick={() => setEditing({ parent: selected })}
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
                        <SegmentedControl
                          aria-label={t`Location scope`}
                          value={direct ? 'direct' : 'descendants'}
                          onChange={(value) =>
                            setParams((previous) => {
                              const next = new URLSearchParams(previous);
                              next.set('scope', value);
                              return next;
                            })
                          }
                          data={[
                            {
                              value: 'descendants',
                              label: t`Including sublocations`
                            },
                            { value: 'direct', label: t`Direct only` }
                          ]}
                        />
                      </Stack>
                    </Paper>
                    <SimpleGrid cols={{ base: 1, sm: 3 }}>
                      <Paper withBorder p='md'>
                        <Text size='sm' c='dimmed'>{t`Machines now`}</Text>
                        <Text size='xl' fw={700}>
                          {countMachines ?? '—'}
                        </Text>
                      </Paper>
                      <Paper withBorder p='md'>
                        <Text
                          size='sm'
                          c='dimmed'
                        >{t`Direct at this location`}</Text>
                        <Text size='xl' fw={700}>
                          {counts?.direct_machines ?? '—'}
                        </Text>
                      </Paper>
                      <Paper withBorder p='md'>
                        <Text
                          size='sm'
                          c='dimmed'
                        >{t`Open work orders now`}</Text>
                        <Text size='xl' fw={700}>
                          {countOrders ?? '—'}
                        </Text>
                        <Text
                          size='xs'
                          c='dimmed'
                        >{t`Drafts excluded; work orders counted once.`}</Text>
                      </Paper>
                    </SimpleGrid>
                    <ScopedMachineList
                      key={`${selected.pk}-${direct}`}
                      location={selected.pk}
                      includeDescendants={!direct}
                      canChange={context.data.can_change}
                    />
                    <Text
                      size='sm'
                      c='dimmed'
                    >{t`Historical performance will appear when validated event and placement history is available. Current placement does not establish past downtime.`}</Text>
                  </>
                ) : (
                  !locationId && (
                    <Paper withBorder p='xl'>
                      <Stack align='center'>
                        <IconBuildingFactory2 size={38} />
                        <Title order={4}>{t`Choose a location`}</Title>
                        <Text
                          c='dimmed'
                          ta='center'
                        >{t`Select a site or sublocation to see its machines and current maintenance counts.`}</Text>
                        <Button
                          component={Link}
                          to='/machines/index/unassigned/'
                          variant='light'
                        >{t`View unassigned machines`}</Button>
                      </Stack>
                    </Paper>
                  )
                )}
              </Stack>
            </Grid.Col>
          </Grid>
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
