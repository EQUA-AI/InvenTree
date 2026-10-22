import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Box,
  Breadcrumbs,
  Button,
  Checkbox,
  Collapse,
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
  Tooltip,
  useMatches
} from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import {
  IconFlag,
  IconMapPin,
  IconPlus,
  IconSearch,
  IconServerOff
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { MachineAlarmBadges } from '../../../components/items/MachineAlarmBadges';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';
import { StatusBadge as Badge, HealthStateBadge } from '../health/common';
import { formatInstant, useDisplayClock } from '../health/mimic/observedTime';
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
  const phone = useMatches({ base: true, sm: false });
  const api = useApi();
  const identity = useUserState((s) => s.authGeneration);
  const { search, alarmsOnly, criticality, page } = state;
  const [debounced] = useDebouncedValue(search, 200);
  const searchPending = search !== debounced;
  const [filtersOpen, setFiltersOpen] = useState(false);
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
  const emptyKind = machineEmptyKind({
    searched: !!debounced.trim(),
    alarmsOnly,
    filtered: !!criticality,
    unassigned,
    locationSelected: location != null,
    includeDescendants
  });
  return (
    <Stack
      gap='sm'
      className={`${operations.phoneStack} ${classes.machineList}`}
    >
      {query.data && !query.isError && !searchPending && !pageOutOfRange && (
        <MachinePageSummary
          rows={query.data.results}
          total={query.data.count}
          updatedAt={query.dataUpdatedAt}
          updating={query.isFetching}
          alarmsOnly={alarmsOnly}
          onAlarmsChange={() => onStateChange({ alarmsOnly: !alarmsOnly })}
        />
      )}

      <Button
        hiddenFrom='sm'
        variant='default'
        size='xs'
        aria-expanded={filtersOpen}
        onClick={() => setFiltersOpen((value) => !value)}
      >{t`Filters`}</Button>
      <Collapse
        expanded={!phone || filtersOpen}
        display={phone && !filtersOpen ? 'none' : undefined}
      >
        <Group
          justify='space-between'
          align='flex-end'
          className={classes.listToolbar}
        >
          <Group align='flex-end' className={classes.listFilters}>
            <TextInput
              size='xs'
              label={t`Search machines`}
              classNames={{
                input: operations.input,
                description: operations.meta
              }}
              placeholder={t`Name, serial or manufacturer`}
              leftSection={<IconSearch size={16} />}
              value={search}
              onChange={(event) =>
                onStateChange({ search: event.currentTarget.value }, true)
              }
            />
            <Switch
              classNames={{ track: operations.input }}
              label={t`In alarm only`}
              checked={alarmsOnly}
              onChange={(event) =>
                onStateChange({ alarmsOnly: event.currentTarget.checked })
              }
            />
            <Select
              size='xs'
              label={t`Criticality`}
              classNames={{
                input: operations.input,
                description: operations.meta
              }}
              // The chosen value names its filter; the options stay short.
              data={[
                { value: 'any', label: t`Any criticality` },
                ...MACHINE_CRITICALITIES.map((level) => ({
                  value: level,
                  label: `${t`Criticality`}: ${criticalityLabel(level)}`
                }))
              ]}
              renderOption={({ option }) =>
                option.value === 'any'
                  ? t`Any criticality`
                  : criticalityLabel(option.value as MachineCriticality)
              }
              value={criticality || 'any'}
              onChange={(value) =>
                onStateChange({ criticality: machineCriticality(value) ?? '' })
              }
              w={190}
            />
          </Group>
          <Group>
            <Text size='sm' className={operations.meta}>
              {query.data && !query.isError && !searchPending
                ? t`${query.data.count} machines`
                : ''}
            </Text>
            {selection.length > 0 && (
              <Text size='sm' className={operations.meta}>
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
      </Collapse>
      {(query.isPending ||
        searchPending ||
        (pageOutOfRange && !query.isError)) && <Loader size='sm' />}
      {query.isFetching &&
        query.data &&
        !query.isError &&
        !searchPending &&
        !pageOutOfRange && (
          <Text
            size='sm'
            className={operations.meta}
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
          {query.data.results.length > 0 && (
            <Box data-testid='machine-results'>
              {!phone && (
                <Box>
                  {query.data.results.length > 0 && (
                    <Table.ScrollContainer
                      minWidth={720}
                      scrollAreaProps={{ type: 'auto' }}
                    >
                      <Table
                        className={classes.machineTable}
                        striped
                        highlightOnHover
                        verticalSpacing='sm'
                        style={{ tableLayout: 'fixed' }}
                      >
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th w='40%'>
                              <Group gap='xs' wrap='nowrap'>
                                {canChange && (
                                  <Checkbox
                                    className={classes.machineCheckbox}
                                    classNames={{ input: operations.input }}
                                    aria-label={t`Select machines on this page`}
                                    checked={allSelected}
                                    indeterminate={
                                      selected.length > 0 && !allSelected
                                    }
                                    onChange={(event) =>
                                      setSelection(
                                        event.currentTarget.checked
                                          ? query.data.results.map(
                                              (machine) => machine.pk
                                            )
                                          : []
                                      )
                                    }
                                  />
                                )}
                                {t`Machine`}
                              </Group>
                            </Table.Th>
                            {/* Short status labels must read whole on a phone; the
                      table scrolls sideways rather than truncating them. */}
                            <Table.Th w='18%'>
                              <Tooltip
                                multiline
                                maw={320}
                                label={t`The Health tab's assessment of this machine`}
                              >
                                <Text
                                  component='span'
                                  tabIndex={0}
                                >{t`Condition`}</Text>
                              </Tooltip>
                            </Table.Th>
                            <Table.Th w='15%' ta='right'>
                              <Tooltip
                                multiline
                                maw={320}
                                label={t`Active (open or acknowledged) alarms on this machine and the pumps under it`}
                              >
                                <Text
                                  component='span'
                                  tabIndex={0}
                                >{t`Alarms`}</Text>
                              </Tooltip>
                            </Table.Th>
                            <Table.Th w='17%' ta='right'>{t`Work`}</Table.Th>
                            <Table.Th w='10%'>{t`Criticality`}</Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {query.data.results.map((machine) => (
                            <Table.Tr
                              key={machine.pk}
                              data-machine={machine.pk}
                              id={`machine-row-${machine.pk}`}
                              tabIndex={-1}
                            >
                              <Table.Td>
                                <MachineIdentity
                                  machine={machine}
                                  href={machineDetailHref(machine.pk, rowScope)}
                                  showPlacement={
                                    includeDescendants &&
                                    machine.physical_location?.pk !== location
                                  }
                                  canChange={canChange}
                                  checked={selection.includes(machine.pk)}
                                  onChecked={(checked) =>
                                    setSelection(
                                      checked
                                        ? [...selection, machine.pk]
                                        : selection.filter(
                                            (id) => id !== machine.pk
                                          )
                                    )
                                  }
                                />
                              </Table.Td>
                              <MachineConditionCell value={machine.condition} />
                              <Table.Td
                                data-alarms={machine.open_alarms ?? undefined}
                                ta='right'
                              >
                                {typeof machine.open_alarms !== 'number' ||
                                !Number.isSafeInteger(machine.open_alarms) ||
                                machine.open_alarms < 0 ? (
                                  <MachineMissingValue
                                    reason={t`Alarm count not supplied`}
                                  />
                                ) : machine.open_alarms > 0 ? (
                                  <MachineAlarmBadges
                                    open={machine.open_alarms}
                                    critical={machine.open_critical_alarms}
                                    warning={machine.open_warning_alarms}
                                    justify='flex-end'
                                  />
                                ) : (
                                  '0'
                                )}
                              </Table.Td>
                              <MachineWorkCell
                                value={machine.open_work}
                                href={machineDetailHref(machine.pk, rowScope)}
                              />
                              <MachineCriticalityCell
                                value={machine.criticality}
                              />
                            </Table.Tr>
                          ))}
                        </Table.Tbody>
                      </Table>
                    </Table.ScrollContainer>
                  )}
                </Box>
              )}
              {phone && (
                <Stack gap='xs'>
                  {query.data.results.map((machine) => (
                    <Paper
                      key={machine.pk}
                      withBorder
                      p='sm'
                      data-machine-card={machine.pk}
                      data-machine={machine.pk}
                      id={`machine-row-${machine.pk}`}
                      tabIndex={-1}
                    >
                      <MachineIdentity
                        machine={machine}
                        href={machineDetailHref(machine.pk, rowScope)}
                        showPlacement={
                          includeDescendants &&
                          machine.physical_location?.pk !== location
                        }
                        canChange={canChange}
                        checked={selection.includes(machine.pk)}
                        onChecked={(checked) =>
                          setSelection(
                            checked
                              ? [...selection, machine.pk]
                              : selection.filter((id) => id !== machine.pk)
                          )
                        }
                      />
                      <Table
                        className={classes.phoneFacts}
                        withRowBorders={false}
                        verticalSpacing={4}
                      >
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th>{t`Condition`}</Table.Th>
                            <Table.Th>{t`Alarms`}</Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          <Table.Tr>
                            <MachineConditionCell value={machine.condition} />
                            <Table.Td
                              data-alarms={machine.open_alarms ?? undefined}
                            >
                              {typeof machine.open_alarms === 'number' ? (
                                machine.open_alarms === 0 ? (
                                  '0'
                                ) : (
                                  <MachineAlarmBadges
                                    open={machine.open_alarms}
                                    critical={machine.open_critical_alarms}
                                    warning={machine.open_warning_alarms}
                                  />
                                )
                              ) : (
                                <MachineMissingValue
                                  reason={t`Alarm count not supplied`}
                                />
                              )}
                            </Table.Td>
                          </Table.Tr>
                          <Table.Tr>
                            <Table.Td>{t`Work`}</Table.Td>
                            <MachineWorkCell
                              value={machine.open_work}
                              href={machineDetailHref(machine.pk, rowScope)}
                            />
                          </Table.Tr>
                          <Table.Tr>
                            <Table.Td>{t`Criticality`}</Table.Td>
                            <MachineCriticalityCell
                              value={machine.criticality}
                            />
                          </Table.Tr>
                        </Table.Tbody>
                      </Table>
                    </Paper>
                  ))}
                </Stack>
              )}
            </Box>
          )}
          {!query.data.results.length && (
            <Paper withBorder p='lg'>
              {emptyKind === 'no-search-matches' && (
                <Stack gap='xs'>
                  <Text
                    className={operations.meta}
                  >{t`No machines match your search.`}</Text>
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
                  <Text
                    className={operations.meta}
                  >{t`No machines match these filters.`}</Text>
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
                  <Text className={operations.meta}>
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
                  <Text className={operations.meta}>
                    {t`No machines are placed directly at this location.`}
                  </Text>
                  <Text size='sm' className={operations.meta}>
                    {t`Turn on Include sublocations to see machines placed in sublocations.`}
                  </Text>
                </Stack>
              )}
              {emptyKind === 'no-machines-here' && (
                <Stack gap='xs'>
                  <IconServerOff />
                  <Title order={5}>{t`No machines at this location`}</Title>
                  <Text
                    className={operations.meta}
                  >{t`No machine is placed at this location or its sublocations.`}</Text>
                  <Anchor
                    className={operations.link}
                    component={Link}
                    to='/machines/index/machines/'
                  >{t`Browse all machines`}</Anchor>
                </Stack>
              )}
              {emptyKind === 'no-unassigned-machines' && (
                <Text className={operations.meta}>
                  {t`No unassigned machines in your workspace.`}
                </Text>
              )}
            </Paper>
          )}
          {query.data.count > MACHINE_PAGE_SIZE && (
            <nav aria-label={t`Machine results pagination`}>
              <Pagination
                color='var(--mantine-primary-color-7)'
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

function MachineMissingValue({ reason }: Readonly<{ reason: string }>) {
  return (
    <Tooltip
      multiline
      maw={320}
      label={reason}
      events={{ hover: true, focus: true, touch: true }}
    >
      <Text
        component='span'
        size='sm'
        tabIndex={0}
        aria-label={reason}
        className={classes.missingValue}
      >
        —
      </Text>
    </Tooltip>
  );
}

function MachineIdentity({
  machine,
  href,
  showPlacement,
  canChange,
  checked,
  onChecked
}: Readonly<{
  machine: LocatedMachine;
  href: string;
  showPlacement: boolean;
  canChange: boolean;
  checked: boolean;
  onChecked: (checked: boolean) => void;
}>) {
  const identity = [machine.serial, machine.manufacturer, machine.model]
    .filter(Boolean)
    .join(' · ');
  return (
    <Group
      gap='xs'
      wrap='nowrap'
      align='center'
      className={classes.machineIdentity}
    >
      {canChange && (
        <Checkbox
          className={classes.machineCheckbox}
          classNames={{ input: operations.input }}
          aria-label={t`Select ${machine.name}`}
          checked={checked}
          onChange={(event) => onChecked(event.currentTarget.checked)}
        />
      )}
      <Stack gap={3} miw={0}>
        <Anchor className={operations.link} component={Link} to={href} fw={600}>
          {machine.name}
        </Anchor>
        {identity && (
          <Text size='xs' className={operations.meta}>
            {identity}
          </Text>
        )}
        {showPlacement && (
          <Text size='xs' className={operations.meta}>
            {machine.physical_location ? (
              locationPath(machine.physical_location)
            ) : (
              <MachineMissingValue reason={t`Physical location not assigned`} />
            )}
          </Text>
        )}
        {!machine.active && (
          <Badge
            color='gray'
            variant='outline'
            size='sm'
          >{t`Inactive record`}</Badge>
        )}
      </Stack>
    </Group>
  );
}

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
      <Table.Td data-open-work='unavailable' ta='right'>
        <Text
          size='sm'
          className={operations.meta}
          title={t`Open work could not be checked`}
        >
          {t`Unavailable · could not be checked`}
        </Text>
      </Table.Td>
    );
  }
  if (summary.kind === 'none') {
    return (
      <Table.Td data-open-work='0' aria-label={t`No open work`} ta='right'>
        0
      </Table.Td>
    );
  }
  const priority = summary.priority;
  return (
    <Table.Td data-open-work={summary.count} ta='right'>
      <Group gap={6} wrap='wrap' justify='flex-end'>
        <Anchor
          className={operations.link}
          component={Link}
          to={machineOpenWorkHref(href)}
          size='sm'
          aria-label={t`${summary.count} open work orders — open the machine's open work`}
        >
          {summary.count}
        </Anchor>
        {priority && (
          <Tooltip
            multiline
            maw={320}
            label={t`Highest open work priority`}
            events={{ hover: true, focus: true, touch: true }}
          >
            <Box tabIndex={0} aria-label={t`Highest open work priority`}>
              <Badge
                size='xs'
                variant='outline'
                c='var(--mantine-color-text)'
                leftSection={<IconFlag size={12} />}
                color={PRIORITY_COLORS[priority]}
                style={{ flexShrink: 0, overflow: 'visible' }}
              >
                {priority === 'high'
                  ? t`High`
                  : priority === 'medium'
                    ? t`Medium`
                    : t`Low`}
              </Badge>
            </Box>
          </Tooltip>
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
 * R6 UI01: preserve the Health classification while keeping coverage available
 * on focus/hover. Missing assessments remain a visible failed read; an unknown
 * assessment is never presented as normal.
 */
function MachineConditionCell({ value }: Readonly<{ value: unknown }>) {
  const condition = machineCondition(value);
  if (condition.kind === 'unavailable') {
    return (
      <Table.Td data-condition='unavailable'>
        <Text
          size='sm'
          className={operations.meta}
          title={t`Condition could not be checked`}
        >
          {t`Condition unavailable · could not be checked`}
        </Text>
      </Table.Td>
    );
  }
  const note = coverageNote(condition.coverage);
  if (condition.state === 'unknown')
    return (
      <Table.Td data-condition='unknown'>
        <MachineMissingValue reason={note ?? t`Condition unknown`} />
      </Table.Td>
    );
  return (
    <Table.Td data-condition={condition.state}>
      <Tooltip
        multiline
        maw={320}
        disabled={!note}
        label={note ?? ''}
        events={{ hover: true, focus: true, touch: true }}
      >
        <Box
          tabIndex={note ? 0 : undefined}
          aria-label={note ?? undefined}
          w='fit-content'
        >
          <HealthStateBadge state={condition.state} size='sm' />
        </Box>
      </Tooltip>
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
 * not read as an alarm severity or a work priority. A missing classification
 * keeps its reason on a focused dash rather than defaulting to a level.
 */
function MachineCriticalityCell({ value }: Readonly<{ value: unknown }>) {
  const level = machineCriticality(value);
  return (
    <Table.Td data-criticality={level ?? 'undeclared'}>
      <Text
        size='sm'
        className={operations.meta}
        style={{ whiteSpace: 'nowrap' }}
      >
        {level ? (
          criticalityLabel(level)
        ) : (
          <MachineMissingValue reason={t`Criticality not declared`} />
        )}
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
            <Anchor
              className={operations.link}
              component={Link}
              to={locationHref(node.pk)}
            >
              {node.name}
            </Anchor>
            <Group gap='xs'>
              <Badge variant='light'>
                {locationKindLabel(locationKind(node.kind))}
              </Badge>
              <Text size='xs' className={operations.meta}>
                {node.code}
              </Text>
              {node.archived && (
                <Badge size='xs' color='gray' variant='light'>
                  {t`Archived`}
                </Badge>
              )}
              {workspaces.length > 1 && (
                <Text size='xs' className={operations.meta}>
                  {workspaceLabel(node.client, workspaces)}
                </Text>
              )}
            </Group>
          </Group>
        ))}
        {(rootQuery.data?.count ?? 0) > ROOT_PAGE_SIZE && (
          <nav aria-label={t`Location overview pagination`}>
            <Pagination
              color='var(--mantine-primary-color-7)'
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
  const [detailsOpen, setDetailsOpen] = useState(false);

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
  const clock = useDisplayClock(context.data?.plant_timezone);
  return (
    <Stack gap='xs' data-operational-panel='machines'>
      {(!selected || context.data?.can_add) && (
        <Group justify='space-between'>
          <Box>
            {view !== 'sites' && (
              <Title order={4}>
                {view === 'all' ? t`All Machines` : t`Unassigned machines`}
              </Title>
            )}
          </Box>
          <Group>
            {view === 'sites' &&
              !selected &&
              context.data &&
              !context.isError && (
                <Button
                  hiddenFrom='lg'
                  variant='default'
                  leftSection={<IconMapPin size={16} />}
                  aria-expanded={mobileOpen}
                  aria-controls='machine-location-browser'
                  onClick={() => setMobileOpen((open) => !open)}
                >
                  {t`Choose location`}
                </Button>
              )}

            {!selected && (
              <Button
                variant='default'
                onClick={() =>
                  queryClient.invalidateQueries({
                    queryKey: ['asset-locations']
                  })
                }
              >{t`Refresh`}</Button>
            )}
            {view === 'sites' && !context.isError && context.data?.can_add && (
              <Button
                leftSection={<IconPlus size={16} />}
                onClick={() => setEditing({})}
              >{t`Add location`}</Button>
            )}
          </Group>
        </Group>
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
                visibleFrom={mobileOpen ? undefined : 'lg'}
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
                <Stack gap='xs' className={operations.phoneStack}>
                  {rawLocation && !locationId && (
                    <Alert color='red'>{t`The location link is invalid.`}</Alert>
                  )}
                  {detail.isFetching && locationId && <Loader size='sm' />}
                  {detail.isError && (
                    <Alert color='red'>{t`This location is unavailable or you no longer have access.`}</Alert>
                  )}
                  {selected && !detail.isError ? (
                    <>
                      {selected.path.length > 1 && (
                        <nav aria-label={t`Location path`}>
                          <Breadcrumbs>
                            {selected.path.map((node) => (
                              <Anchor
                                className={operations.link}
                                key={node.pk}
                                component={Link}
                                to={`?${machineLocationParams(params, node.pk)}`}
                              >
                                {node.name}
                              </Anchor>
                            ))}
                          </Breadcrumbs>
                        </nav>
                      )}
                      <Paper
                        className={operations.band}
                        component='section'
                        aria-labelledby='location-name'
                      >
                        <Group justify='space-between' gap={4}>
                          <Group
                            gap={4}
                            wrap='wrap'
                            w={{ base: '100%', sm: 'auto' }}
                          >
                            <Title
                              w={{ base: '100%', sm: 'auto' }}
                              order={4}
                              id='location-name'
                            >
                              {selected.name}
                            </Title>
                          </Group>
                          <Group gap={4} className={operations.phoneActions}>
                            <Button
                              hiddenFrom='lg'
                              size='compact-xs'
                              className={operations.phoneTarget}
                              variant='default'
                              aria-expanded={mobileOpen}
                              aria-controls='machine-location-browser'
                              onClick={() => setMobileOpen((value) => !value)}
                            >{t`Choose location`}</Button>
                            <Button
                              size='compact-xs'
                              className={operations.phoneTarget}
                              variant='default'
                              onClick={() =>
                                queryClient.invalidateQueries({
                                  queryKey: ['asset-locations']
                                })
                              }
                            >{t`Refresh`}</Button>
                            <Button
                              size='compact-xs'
                              variant='subtle'
                              className={`${operations.link} ${operations.phoneTarget}`}
                              aria-expanded={detailsOpen}
                              onClick={() => setDetailsOpen((value) => !value)}
                            >{t`Location details`}</Button>
                            {context.data.can_add && !selected.archived && (
                              <Button
                                size='compact-xs'
                                className={operations.phoneTarget}
                                variant='default'
                                onClick={() => setEditing({ parent: selected })}
                              >{t`Add sublocation`}</Button>
                            )}
                            {context.data.can_change && (
                              <Button
                                size='compact-xs'
                                className={operations.phoneTarget}
                                variant='default'
                                onClick={() => setEditing({ node: selected })}
                              >{t`Edit location`}</Button>
                            )}
                          </Group>
                          <Switch
                            size='xs'
                            label={t`Include sublocations`}
                            aria-label={t`Include sublocations`}
                            aria-description={
                              direct
                                ? t`Direct placements only`
                                : t`Descendant placements included`
                            }
                            classNames={{
                              track: operations.input,
                              description: operations.meta
                            }}
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
                        </Group>
                        <Group
                          gap='lg'
                          className={classes.locationTotals}
                          data-testid={
                            summary.locationTotalsOnly
                              ? 'location-totals-note'
                              : undefined
                          }
                          aria-label={
                            summary.locationTotalsOnly
                              ? t`Location totals — machine search and alarm filters do not change these counts.`
                              : t`Location totals`
                          }
                        >
                          <Tooltip
                            multiline
                            maw={320}
                            label={t`${summary.direct ?? '—'} machines directly here; includes the selected location scope`}
                            events={{ hover: true, focus: true, touch: true }}
                          >
                            <Text tabIndex={0} size='sm'>
                              {t`Machines`} ·{' '}
                              <Text
                                component='span'
                                data-testid='machine-count-view'
                              >
                                {summary.view ?? (
                                  <MachineMissingValue
                                    reason={t`Machine count not supplied`}
                                  />
                                )}
                              </Text>
                            </Text>
                          </Tooltip>
                          <Text size='sm'>
                            {countOrders ?? (
                              <MachineMissingValue
                                reason={t`Open work count not supplied`}
                              />
                            )}{' '}
                            {t`open work`}
                          </Text>
                        </Group>
                        <Collapse expanded={detailsOpen}>
                          <Stack gap={4} pt='sm'>
                            <Text className={operations.meta}>
                              {locationKindLabel(locationKind(selected.kind))} ·{' '}
                              {selected.code}
                            </Text>
                            {selected.description && (
                              <Text className={operations.meta}>
                                {selected.description}
                              </Text>
                            )}
                            <Text className={operations.meta}>
                              {t`Times`}: {clock.label}
                            </Text>
                            <Text className={operations.meta}>
                              {t`Records read`}{' '}
                              {formatInstant(
                                detail.dataUpdatedAt || null,
                                clock
                              ) ?? '—'}
                            </Text>
                          </Stack>
                        </Collapse>
                      </Paper>
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
