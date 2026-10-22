import { plural, t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Box,
  Breadcrumbs,
  Button,
  Checkbox,
  type CheckboxProps,
  Collapse,
  Grid,
  Group,
  Loader,
  Badge as MantineBadge,
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
  VisuallyHidden,
  useMatches
} from '@mantine/core';
import { useDebouncedValue, useId } from '@mantine/hooks';
import {
  IconChevronRight,
  IconFlag,
  IconMapPin,
  IconPlus,
  IconSearch,
  IconServerOff
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { RefreshAction } from '../../../components/buttons/RefreshAction';
import { MachineAlarmBadges } from '../../../components/items/MachineAlarmBadges';
import {
  FailedValue,
  MissingValue,
  ValueTooltip
} from '../../../components/items/ValueTooltip';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';
import { Timestamp } from '../health/Timestamp';
import { StatusBadge as Badge, HealthStateBadge } from '../health/common';
import { useDisplayClock } from '../health/mimic/observedTime';
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
  locationApi
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
  const { search, alarmsOnly, criticality, page, showInactive = false } = state;
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
    showInactive,
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
      showInactive,
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
            active: showInactive ? undefined : true,
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
  const pageSummary = query.data &&
    !query.isError &&
    !searchPending &&
    !pageOutOfRange && (
      <MachinePageSummary
        rows={query.data.results}
        total={query.data.count}
        updatedAt={query.dataUpdatedAt}
        updating={query.isFetching}
        alarmsOnly={alarmsOnly}
        onAlarmsChange={() => onStateChange({ alarmsOnly: !alarmsOnly })}
        showCount={
          location == null || !!debounced.trim() || alarmsOnly || !!criticality
        }
        compact={!phone}
      />
    );
  return (
    <Stack
      gap='sm'
      className={`${operations.phoneStack} ${classes.machineList}`}
    >
      {phone && pageSummary}

      <Group hiddenFrom='sm' grow wrap='nowrap'>
        <Button
          h={44}
          variant='default'
          size='xs'
          aria-expanded={filtersOpen}
          onClick={() => setFiltersOpen((value) => !value)}
        >{t`Filters`}</Button>
        {location == null && (
          <RefreshAction
            loading={query.isFetching}
            onClick={() => query.refetch()}
          />
        )}
      </Group>
      <Collapse
        expanded={!phone || filtersOpen}
        display={phone && !filtersOpen ? 'none' : undefined}
      >
        <Group
          justify='space-between'
          align='flex-end'
          className={classes.listToolbar}
        >
          {!phone && pageSummary}
          <Group align='flex-end' className={classes.listFilters}>
            <TextInput
              size='sm'
              aria-label={t`Search machines`}
              classNames={{
                input: `${operations.input} ${classes.filterInput}`,
                description: operations.meta
              }}
              placeholder={t`Name, serial or manufacturer`}
              leftSection={<IconSearch size={16} />}
              value={search}
              onChange={(event) =>
                onStateChange({ search: event.currentTarget.value }, true)
              }
            />
            <Box
              h={{ base: 44, sm: 36 }}
              style={{ display: 'flex', alignItems: 'center' }}
            >
              <Switch
                classNames={{
                  track: classes.switchTrack,
                  label: classes.switchLabel
                }}
                label={t`In alarm only`}
                checked={alarmsOnly}
                onChange={(event) =>
                  onStateChange({ alarmsOnly: event.currentTarget.checked })
                }
              />
            </Box>
            <Checkbox
              label={t`Show inactive`}
              styles={{
                body: { alignItems: 'center' },
                label: {
                  minHeight: phone ? 44 : undefined,
                  display: 'flex',
                  alignItems: 'center'
                }
              }}
              checked={showInactive}
              onChange={(event) =>
                onStateChange({ showInactive: event.currentTarget.checked })
              }
            />
            <Select
              size='sm'
              aria-label={t`Criticality`}
              classNames={{
                input: `${operations.input} ${classes.filterInput}`,
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
              {query.data?.count === 0 && !query.isError && !searchPending
                ? t`0 machines`
                : ''}
            </Text>
            {selection.length > 0 && (
              <Text size='sm' className={operations.meta}>
                {t`Selected on this page`} ({selection.length})
              </Text>
            )}
            {canChange && selected.length > 0 && (
              <Button
                variant='light'
                h={{ base: 44, sm: 36 }}
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
                            <Table.Th w='34%'>
                              <Group gap='xs' wrap='nowrap'>
                                {canChange && (
                                  <MachineSelection
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
                                  inherit
                                  className={classes.help}
                                  tabIndex={0}
                                >{t`Condition`}</Text>
                              </Tooltip>
                            </Table.Th>
                            <Table.Th w='16%' ta='left'>
                              <Tooltip
                                multiline
                                maw={320}
                                label={t`Active (open or acknowledged) alarms on this machine and the pumps under it`}
                              >
                                <Text
                                  component='span'
                                  inherit
                                  className={classes.help}
                                  tabIndex={0}
                                >{t`Alarms`}</Text>
                              </Tooltip>
                            </Table.Th>
                            <Table.Th w='18%' ta='left'>{t`Work`}</Table.Th>
                            <Table.Th w='14%'>{t`Criticality`}</Table.Th>
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
                                  scopeLocation={location}
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
                                data-label={t`Alarms`}
                                data-alarms={
                                  machine.open_alarms ?? 'unavailable'
                                }
                                ta='left'
                              >
                                {typeof machine.open_alarms !== 'number' ||
                                !Number.isSafeInteger(machine.open_alarms) ||
                                machine.open_alarms < 0 ? (
                                  <MissingValue
                                    reason={t`Alarm count not supplied`}
                                  />
                                ) : machine.open_alarms > 0 ? (
                                  <MachineAlarmBadges
                                    open={machine.open_alarms}
                                    critical={machine.open_critical_alarms}
                                    warning={machine.open_warning_alarms}
                                    justify='flex-start'
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
                        scopeLocation={location}
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
                              data-label={t`Alarms`}
                              data-alarms={machine.open_alarms ?? 'unavailable'}
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
                                <MissingValue
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

/** The label supplies the hit area while the native checkbox stays 20px. */
function MachineSelection(props: Readonly<CheckboxProps>) {
  const id = useId(props.id);
  return (
    <label className={classes.machineCheckbox} htmlFor={id}>
      <Checkbox
        {...props}
        id={id}
        size='20px'
        classNames={{ input: classes.checkboxInput }}
      />
    </label>
  );
}

function MachineIdentity({
  machine,
  href,
  showPlacement,
  scopeLocation,
  canChange,
  checked,
  onChecked
}: Readonly<{
  machine: LocatedMachine;
  href: string;
  showPlacement: boolean;
  scopeLocation?: number | null;
  canChange: boolean;
  checked: boolean;
  onChecked: (checked: boolean) => void;
}>) {
  const condition = machineCondition(machine.condition);
  const work = machineWorkSummary(machine.open_work);
  const contextReason = [
    condition.kind === 'unavailable'
      ? t`Condition could not be checked`
      : (coverageNote(condition.coverage) ??
        (condition.state === 'normal'
          ? t`Assessment coverage is not supplied by this list.`
          : null)),
    work.kind === 'unavailable' ? t`Open work could not be checked` : null,
    !machineCriticality(machine.criticality)
      ? t`Criticality not declared`
      : null,
    showPlacement && !machine.physical_location
      ? t`Physical location not assigned`
      : null
  ]
    .filter(Boolean)
    .join(' · ');
  const identity = [
    machine.serial,
    showPlacement &&
      machine.physical_location &&
      machine.physical_location.path
        .slice(
          Math.max(
            0,
            machine.physical_location.path.findIndex(
              (node) => node.pk === scopeLocation
            ) + 1
          )
        )
        .map((node) => node.name)
        .join(' / ')
  ]
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
        <MachineSelection
          classNames={{ input: operations.input }}
          aria-label={t`Select ${machine.name}`}
          checked={checked}
          onChange={(event) => onChecked(event.currentTarget.checked)}
        />
      )}
      <Stack gap={3} miw={0}>
        <Tooltip
          label={contextReason}
          disabled={!contextReason}
          multiline
          maw={360}
          events={{ hover: true, focus: true, touch: true }}
        >
          <Anchor
            className={operations.link}
            component={Link}
            to={href}
            fw={600}
          >
            {machine.name}
          </Anchor>
        </Tooltip>
        {identity && (
          <Text size='xs' className={operations.meta}>
            {identity}
          </Text>
        )}
        {showPlacement && !machine.physical_location && (
          <MissingValue reason={t`Physical location not assigned`} />
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
      <Table.Td data-label={t`Work`} data-open-work='unavailable' ta='left'>
        <FailedValue reason={t`Open work could not be checked`} />
      </Table.Td>
    );
  }
  if (summary.kind === 'none') {
    return (
      <Table.Td
        data-label={t`Work`}
        data-open-work='0'
        aria-label={t`No open work`}
        ta='left'
      >
        0
      </Table.Td>
    );
  }
  const priority = summary.priority;
  const priorityLabel =
    priority === 'high' ? t`High` : priority === 'medium' ? t`Medium` : t`Low`;
  return (
    <Table.Td data-label={t`Work`} data-open-work={summary.count} ta='left'>
      <Tooltip
        label={t`Highest open work priority: ${priorityLabel}`}
        disabled={!priority}
        events={{ hover: true, focus: true, touch: true }}
      >
        <Anchor
          className={`${operations.link} ${classes.workLink}`}
          component={Link}
          to={machineOpenWorkHref(href)}
          size='sm'
          aria-label={
            priority
              ? t`${summary.count} open work orders · ${priorityLabel} priority — open the machine's open work`
              : t`${summary.count} open work orders — open the machine's open work`
          }
        >
          <Group component='span' gap={6} wrap='nowrap'>
            {summary.count}
            {priority && (
              <Badge
                size='xs'
                variant='outline'
                c='var(--mantine-color-text)'
                leftSection={<IconFlag size={12} />}
                color={PRIORITY_COLORS[priority]}
              >
                {priorityLabel}
              </Badge>
            )}
          </Group>
        </Anchor>
      </Tooltip>
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
      <Table.Td data-label={t`Condition`} data-condition='unavailable'>
        <FailedValue reason={t`Condition could not be checked`} />
      </Table.Td>
    );
  }
  const note =
    coverageNote(condition.coverage) ??
    (condition.state === 'normal'
      ? t`Assessment coverage is not supplied by this list.`
      : null);
  if (condition.state === 'unknown')
    return (
      <Table.Td data-label={t`Condition`} data-condition='unknown'>
        <MissingValue reason={note ?? t`Condition unknown`} />
      </Table.Td>
    );
  return (
    <Table.Td data-label={t`Condition`} data-condition={condition.state}>
      {note ? (
        <ValueTooltip
          label={note}
          accessibleLabel={`${condition.state === 'normal' ? t`Normal` : condition.state} · ${note}`}
        >
          <HealthStateBadge
            compactCoverage
            state={condition.state}
            size='sm'
            partial={
              condition.state === 'normal' && condition.coverage.kind !== 'full'
            }
            coverageUnknown={
              condition.state === 'normal' && condition.coverage.kind === 'full'
            }
          />
        </ValueTooltip>
      ) : (
        <HealthStateBadge
          compactCoverage
          state={condition.state}
          size='sm'
          partial={
            condition.state === 'normal' && condition.coverage.kind !== 'full'
          }
          coverageUnknown={
            condition.state === 'normal' && condition.coverage.kind === 'full'
          }
        />
      )}
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
    <Table.Td
      data-label={t`Criticality`}
      data-criticality={level ?? 'undeclared'}
    >
      <Text
        size='sm'
        className={operations.meta}
        style={{ whiteSpace: 'nowrap' }}
      >
        {level ? (
          criticalityLabel(level)
        ) : (
          <MissingValue reason={t`Criticality not declared`} />
        )}
      </Text>
    </Table.Td>
  );
}

/** Paginated root-location overview for the unselected main pane (M1). */
function RootOverview({
  identity,
  workspaces,
  showArchived
}: {
  showArchived: boolean;
  identity: number;
  workspaces: { pk: number; name: string }[];
}) {
  const api = useApi();
  const [params] = useSearchParams();
  const [rootPage, setRootPage] = useState(1);
  const rootQuery = useQuery<PageResult<LocationNode>>({
    queryKey: [
      'asset-locations',
      identity,
      'root-overview',
      showArchived,
      rootPage
    ],
    queryFn: async ({ signal }) =>
      (
        await api.get(locationApi, {
          signal,
          params: {
            parent: 'root',
            active_only: !showArchived,
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
    <Paper withBorder p='md'>
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
        <ul aria-label={t`Top-level locations`} className={classes.rootList}>
          {rootQuery.data?.results.map((node) => (
            <li key={node.pk} className={classes.rootRow}>
              <Anchor
                className={operations.link}
                component={Link}
                to={locationHref(node.pk)}
              >
                {node.name}
              </Anchor>
              <Group gap={4}>
                <MantineBadge
                  variant='outline'
                  color='gray'
                  c='var(--mantine-color-text)'
                  tt='none'
                >
                  {locationKindLabel(locationKind(node.kind))}
                </MantineBadge>
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
            </li>
          ))}
        </ul>
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
          variant='default'
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
  const showArchived = params.get('show_archived') === 'true';
  const updateArchived = (show: boolean) =>
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (show) next.set('show_archived', 'true');
      else {
        next.delete('show_archived');
        next.delete('location');
      }
      next.delete('page');
      return next;
    });
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
  const choiceFocus = useRef<number | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [refreshResult, setRefreshResult] = useState('');
  const refreshLocations = async () => {
    setRefreshResult('');
    await queryClient.invalidateQueries({ queryKey: ['asset-locations'] });
    const failed = queryClient
      .getQueryCache()
      .findAll({ queryKey: ['asset-locations'], type: 'active' })
      .some((query) => query.state.status === 'error');
    setRefreshResult(
      failed
        ? t`Refresh failed. Current errors are shown.`
        : t`Location and machine records refreshed.`
    );
  };

  const context = useQuery<LocationContext>({
    queryKey: ['asset-locations', identity, 'context'],
    queryFn: async ({ signal }) =>
      (await api.get(`${locationApi}context/`, { signal })).data
  });
  const detail = useQuery<LocationNode>({
    queryKey: [
      'asset-locations',
      identity,
      'detail',
      locationId,
      !!listState.showInactive
    ],
    queryFn: async ({ signal }) =>
      (
        await api.get(`${locationApi}${locationId}/`, {
          params: { active: listState.showInactive ? undefined : true },
          signal
        })
      ).data,
    enabled: view === 'sites' && !!locationId
  });
  const selected = detail.isError ? undefined : detail.data;
  useEffect(() => {
    if (!mobileOpen && selected && choiceFocus.current === selected.pk) {
      choiceFocus.current = null;
      const frame = requestAnimationFrame(() =>
        document.getElementById('location-name')?.focus({ preventScroll: true })
      );
      return () => cancelAnimationFrame(frame);
    }
  }, [selected?.pk, mobileOpen]);
  const select = (id: number | null) => {
    if (id !== null) choiceFocus.current = id;
    setParams((previous) => machineLocationParams(previous, id));
    if (id !== null) setMobileOpen(false);
  };
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
      <VisuallyHidden
        component='output'
        aria-label={t`Refresh result`}
        aria-live='polite'
      >
        {refreshResult}
      </VisuallyHidden>
      {(!selected || context.data?.can_add) && (
        <Group
          justify='flex-end'
          visibleFrom={view === 'sites' ? undefined : 'sm'}
        >
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
                  rightSection={
                    <IconChevronRight
                      size={16}
                      style={{
                        transform: mobileOpen ? 'rotate(90deg)' : undefined
                      }}
                    />
                  }
                  aria-controls='machine-location-browser'
                  onClick={() => setMobileOpen((open) => !open)}
                >
                  {t`Choose location`}
                </Button>
              )}

            {!selected && <RefreshAction onClick={refreshLocations} />}
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
                hidden={!!selected && !mobileOpen}
                visibleFrom={mobileOpen ? undefined : 'lg'}
                span={{ base: 12, lg: 4 }}
              >
                <LocationBrowser
                  selected={selected}
                  showArchived={showArchived}
                  onShowArchived={updateArchived}
                  onSelect={select}
                  identity={identity}
                  workspaces={workspaces}
                />
              </Grid.Col>
              <Grid.Col
                span={{ base: 12, lg: selected && !mobileOpen ? 12 : 8 }}
              >
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
                        p={{ base: 8, sm: 12 }}
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
                              tabIndex={-1}
                            >
                              {selected.name}
                            </Title>
                          </Group>
                          <Button.Group
                            className={operations.phoneActions}
                            style={{ flexWrap: 'wrap' }}
                          >
                            <Button
                              size='xs'
                              h={36}
                              className={operations.phoneTarget}
                              variant='default'
                              aria-expanded={mobileOpen}
                              rightSection={
                                <IconChevronRight
                                  size={16}
                                  style={{
                                    transform: mobileOpen
                                      ? 'rotate(90deg)'
                                      : undefined
                                  }}
                                />
                              }
                              aria-controls='machine-location-browser'
                              onClick={() => setMobileOpen((value) => !value)}
                            >{t`Choose location`}</Button>
                            <RefreshAction onClick={refreshLocations} />
                            <Button
                              size='xs'
                              h={36}
                              variant='default'
                              className={operations.phoneTarget}
                              aria-expanded={detailsOpen}
                              onClick={() => setDetailsOpen((value) => !value)}
                            >{t`Location details`}</Button>
                            {context.data.can_add && !selected.archived && (
                              <Button
                                size='xs'
                                h={36}
                                className={operations.phoneTarget}
                                variant='default'
                                onClick={() => setEditing({ parent: selected })}
                              >{t`Add sublocation`}</Button>
                            )}
                            {context.data.can_change && (
                              <Button
                                size='xs'
                                h={36}
                                className={operations.phoneTarget}
                                variant='default'
                                onClick={() => setEditing({ node: selected })}
                              >{t`Edit location`}</Button>
                            )}
                          </Button.Group>
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
                              track: classes.switchTrack,
                              label: classes.switchLabel,
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
                          gap='sm'
                          className={classes.locationTotals}
                          component='section'
                          aria-label={t`Location totals`}
                        >
                          {summary.view != null ? (
                            <ValueTooltip
                              label={`${direct ? t`Direct placements only` : t`Descendant placements included`} · ${t`Location totals are independent of the loaded machine page and its search, alarm and criticality filters.`}`}
                              focusable={false}
                            >
                              <Group component='span' gap={4} wrap='nowrap'>
                                <span data-testid='machine-count-view'>
                                  {summary.view}
                                </span>
                                <span>
                                  {plural(summary.view, {
                                    one: 'machine',
                                    other: 'machines'
                                  })}
                                </span>
                              </Group>
                            </ValueTooltip>
                          ) : (
                            <MissingValue
                              label={t`Machines`}
                              reason={t`Machine count not supplied`}
                            />
                          )}
                          <Text size='sm'>
                            {countOrders != null ? (
                              plural(countOrders, {
                                one: '# open work order',
                                other: '# open work orders'
                              })
                            ) : (
                              <MissingValue
                                label={t`Open work`}
                                reason={t`Open work count not supplied`}
                              />
                            )}
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
                              {t`Records read`}{' '}
                              <Timestamp
                                value={detail.dataUpdatedAt || null}
                                clock={clock}
                              />
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
                        key={String(showArchived)}
                        showArchived={showArchived}
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
