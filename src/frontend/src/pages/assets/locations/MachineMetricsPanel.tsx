import { t } from '@lingui/core/macro';
import {
  Accordion,
  Alert,
  Anchor,
  Box,
  Button,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { type SetStateAction, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ReadErrorNotice } from '../../../components/common/ReadErrorNotice';
import { metricLabels } from '../../../components/dashboard/widgets/maintenanceMetrics';
import { parseMaintenanceResult } from '../../../components/dashboard/widgets/maintenanceMetricsRead';
import {
  InfoTooltip,
  MissingValue
} from '../../../components/items/ValueTooltip';
import { WorkTableScroll } from '../../../components/tables/WorkTableScroll';
import { useApi } from '../../../contexts/ApiContext';
import {
  InvalidReadResponse,
  readQueryPolicy
} from '../../../functions/readQueryPolicy';
import { HealthStateBadge, ObservedAt } from '../health/common';
import { MachineHistoryPanel } from './MachineHistoryPanel';
import { machineDetailHref } from './locationTree';
import { healthCounts, machineScopeHref } from './machineMetrics';
import {
  useClearDeniedMetrics,
  useMachineMetrics,
  useMetricsAccess,
  useMetricsIdentity
} from './useMachineMetrics';

function MetricCard({
  label,
  value,
  missingReason,
  onClick
}: {
  label: string;
  value: number | undefined;
  missingReason: string;
  onClick: () => void;
}) {
  if (value === undefined)
    return (
      <Paper withBorder p='sm' radius='md'>
        <Text size='sm' c='dimmed'>
          {label}
        </Text>
        <Box fz='xl' fw={700}>
          <MissingValue label={label} reason={missingReason} />
        </Box>
      </Paper>
    );
  return (
    <Paper
      component='button'
      type='button'
      withBorder
      p='sm'
      radius='md'
      onClick={onClick}
      style={{
        textAlign: 'start',
        font: 'inherit',
        color: 'inherit',
        cursor: 'pointer'
      }}
    >
      <Text size='sm' c='dimmed'>
        {label}
      </Text>
      <Text size='xl' fw={700}>
        {value ?? '—'}
      </Text>
    </Paper>
  );
}

export function MachineMetricsPanel(props: {
  locationId?: number;
  descendants: boolean;
  showInactive?: boolean;
}) {
  const identity = useMetricsIdentity();
  // Clear local selections along with payloads when scope or authorization changes.
  return (
    <MachineMetricsContent
      key={JSON.stringify([
        ...identity,
        props.locationId,
        props.descendants,
        props.showInactive
      ])}
      {...props}
    />
  );
}

function MachineMetricsContent({
  locationId,
  descendants,
  showInactive = false
}: { locationId?: number; descendants: boolean; showInactive?: boolean }) {
  const api = useApi();
  const access = useMetricsAccess();
  const { population, health, work, identity } = useMachineMetrics(
    locationId,
    descendants,
    showInactive
  );
  const [attention, setAttention] = useState<'critical' | 'unknown' | null>(
    null
  );
  const [metric, setMetric] = useState('open');
  const [machineId, setMachineId] = useState<string | null>(null);
  const [paging, setPaging] = useState<{
    machine: string | null;
    values: number[];
  }>({ machine: null, values: [0] });
  const heading = useRef<HTMLHeadingElement>(null);
  const scope = { location: locationId ?? null, direct: !descendants };
  const machines = population.isError ? undefined : population.data;
  const summaries =
    !population.isError && !health.isError ? health.data : undefined;
  const counts = summaries
    ? healthCounts(summaries.map((r) => r.summary))
    : undefined;
  const totals =
    !population.isError && !work.isError ? work.data?.totals : undefined;
  const selectedMachine = machines?.some(
    (machine) => String(machine.pk) === machineId
  )
    ? machineId
    : machines?.[0]
      ? String(machines[0].pk)
      : null;
  // Never carry a previous machine's contributor offset into a refreshed population.
  const offsets = paging.machine === selectedMachine ? paging.values : [0];
  const setOffsets = (next: SetStateAction<number[]>) =>
    setPaging({
      machine: selectedMachine,
      values: typeof next === 'function' ? next(offsets) : next
    });
  const offset = offsets[offsets.length - 1];
  const contributors = useQuery({
    ...readQueryPolicy,
    queryKey: [
      'machine-metrics',
      ...identity,
      'maintenance',
      Number(selectedMachine),
      metric,
      offset,
      'current'
    ],
    enabled: !!selectedMachine && !!machines && !work.isError && !access.denied,
    staleTime: 30_000,
    queryFn: async ({ signal }) => {
      const { data } = await api.get('/api/aichat/ui/maintenance-metrics/', {
        signal,
        params: { metric, machine: selectedMachine, offset }
      });
      try {
        return parseMaintenanceResult(data, metric);
      } catch {
        throw new InvalidReadResponse('Invalid maintenance result');
      }
    }
  });
  useClearDeniedMetrics(contributors.error);
  const contributorData =
    !contributors.isError && !population.isError
      ? contributors.data
      : undefined;
  const denied =
    access.denied ||
    (health.error as { response?: { status?: number } })?.response?.status ===
      404 ||
    [population.error, health.error, work.error, contributors.error].some(
      (error) => {
        const status = (error as { response?: { status?: number } })?.response
          ?.status;
        return status === 401 || status === 403;
      }
    );
  const showWork = (value: string) => {
    setMetric(value);
    setOffsets([0]);
    heading.current?.focus();
    heading.current?.scrollIntoView({ block: 'nearest' });
  };
  return (
    <Stack gap='md'>
      <Group justify='space-between'>
        <Group gap={6}>
          <Title order={4}>{t`Needs attention`}</Title>
          <InfoTooltip
            label={t`Location totals. The machine table search does not change these counts.`}
          />
        </Group>
        <Button
          variant='default'
          component={Link}
          to={machineScopeHref('/maintenance/board/', scope)}
        >{t`View in Maintenance`}</Button>
      </Group>
      {population.isPending && (
        <Loader size='sm' aria-label={t`Loading machines`} />
      )}
      {population.isError && (
        <ReadErrorNotice
          error={population.error}
          retry={() => {
            void population.refetch();
          }}
        />
      )}
      {!population.isError && health.isFetching && (
        <Loader size='sm' aria-label={t`Loading health data`} />
      )}
      {health.isError && (
        <ReadErrorNotice
          error={health.error}
          retry={() => {
            void health.refetch();
          }}
        />
      )}
      {work.isError && (
        <ReadErrorNotice
          error={work.error}
          retry={() => {
            void work.refetch();
          }}
        />
      )}
      {access.denied && (
        <ReadErrorNotice
          error={access.error}
          retry={() => {
            void access.retry();
          }}
        />
      )}
      {denied && (
        <Alert color='red'>
          {(access.error as { response?: { status?: number } })?.response
            ?.status === 404 ||
          (health.error as { response?: { status?: number } })?.response
            ?.status === 404
            ? t`Metrics access unavailable`
            : t`Metrics access denied`}
        </Alert>
      )}
      {!denied && (
        <>
          <SimpleGrid cols={3} spacing='xs'>
            <MetricCard
              label={t`Critical machines`}
              value={counts?.critical}
              missingReason={
                health.isError || population.isError
                  ? t`Condition could not be checked`
                  : t`Loading condition`
              }
              onClick={() => setAttention('critical')}
            />
            <MetricCard
              label={t`Overdue open work orders`}
              value={totals?.overdue}
              missingReason={
                work.isError || population.isError
                  ? t`Open work could not be checked`
                  : work.data?.totals === null
                    ? t`Work-order metrics unavailable for this scope`
                    : t`Loading work orders`
              }
              onClick={() => showWork('overdue')}
            />
            <MetricCard
              label={t`Condition unavailable`}
              value={counts?.unknown}
              missingReason={
                health.isError || population.isError
                  ? t`Condition could not be checked`
                  : t`Loading condition`
              }
              onClick={() => setAttention('unknown')}
            />
          </SimpleGrid>
          {machines?.length === 0 && (
            <Text size='sm'>{t`No machines in this location scope.`}</Text>
          )}
          {!!counts?.unconfigured && (
            <Group gap={6}>
              <Text size='sm'>
                {t`Monitoring not configured`}: {counts.unconfigured}
              </Text>
              <InfoTooltip
                label={t`Missing monitoring does not imply healthy equipment.`}
              />
            </Group>
          )}
          {summaries && (
            <Accordion variant='contained'>
              <Accordion.Item value='health'>
                <Accordion.Control>{t`Coverage and condition details`}</Accordion.Control>
                <Accordion.Panel>
                  <Text size='sm'>
                    {t`Machines with stale signals`}: {counts?.stale} ·{' '}
                    {t`Degraded data`}: {counts?.degraded}
                  </Text>
                  <InfoTooltip
                    label={t`Condition includes active anomalies. Configuration, freshness and data quality are separate indicators.`}
                  />
                  <Stack gap='xs' mt='sm'>
                    {summaries
                      .filter(
                        (r) =>
                          !attention ||
                          (attention === 'critical'
                            ? r.summary.state === 'critical'
                            : ['unknown', 'offline'].includes(r.summary.state))
                      )
                      .map(({ machine, summary }) => (
                        <Group key={machine.pk} justify='space-between'>
                          <Anchor
                            component={Link}
                            to={machineDetailHref(machine.pk, {
                              ...scope,
                              source: 'sites'
                            })}
                          >
                            {machine.name}
                          </Anchor>
                          <HealthStateBadge
                            state={summary.state}
                            coverageUnknown={summary.state === 'normal'}
                          />
                          <ObservedAt
                            observedAt={summary.last_observed_at}
                            stale={summary.stale_signal_count > 0}
                          />
                        </Group>
                      ))}
                  </Stack>
                </Accordion.Panel>
              </Accordion.Item>
            </Accordion>
          )}
          {attention && summaries && (
            <Paper
              withBorder
              p='sm'
              component='section'
              aria-label={
                attention === 'critical'
                  ? t`Critical machines`
                  : t`Machines without a current condition`
              }
            >
              <Stack gap='xs'>
                {summaries
                  .filter((r) =>
                    attention === 'critical'
                      ? r.summary.state === 'critical'
                      : ['unknown', 'offline'].includes(r.summary.state)
                  )
                  .map(({ machine, summary }) => (
                    <Group key={machine.pk}>
                      <Anchor
                        component={Link}
                        to={machineDetailHref(machine.pk, {
                          ...scope,
                          source: 'sites'
                        })}
                      >
                        {machine.name}
                      </Anchor>
                      <HealthStateBadge
                        state={summary.state}
                        coverageUnknown={summary.state === 'normal'}
                      />
                    </Group>
                  ))}
                <Button
                  variant='subtle'
                  onClick={() => setAttention(null)}
                >{t`Close`}</Button>
              </Stack>
            </Paper>
          )}
          {work.isPending && !!machines?.length && (
            <Loader size='sm' aria-label={t`Loading work orders`} />
          )}
          {work.data?.totals === null && (
            <Alert color='gray'>{t`Work-order metrics unavailable for this scope.`}</Alert>
          )}
          {totals && (
            <Group gap='lg'>
              <Anchor component='button' onClick={() => showWork('open')}>
                {t`Open work orders`}: {totals.open}
              </Anchor>
              <Text size='sm'>
                {t`Machines with open work`}: {totals.machinesWithOpenWork}
              </Text>
              {totals.reportingDate && (
                <Text size='xs' c='dimmed'>
                  {t`Reporting date`}: {totals.reportingDate} ·{' '}
                  {totals.timezone}
                </Text>
              )}
            </Group>
          )}
        </>
      )}
      <MachineHistoryPanel />
      {!denied && !!machines?.length && (
        <Paper withBorder p='md'>
          <Stack gap='sm'>
            <Title
              order={4}
              ref={heading}
              tabIndex={-1}
            >{t`Contributing work`}</Title>
            <SegmentedControl
              aria-label={t`Contributing work filter`}
              value={metric}
              onChange={showWork}
              data={[
                { value: 'open', label: t`Open` },
                { value: 'overdue', label: t`Overdue` }
              ]}
            />
            <Select
              aria-label={t`Contributing machine`}
              data={machines.map((m) => ({
                value: String(m.pk),
                label: m.name
              }))}
              value={selectedMachine}
              onChange={(id) => {
                setMachineId(id);
                setOffsets([0]);
              }}
            />
            {contributors.isPending && <Loader size='sm' />}
            {contributors.isError && (
              <ReadErrorNotice
                error={contributors.error}
                retry={() => {
                  void contributors.refetch();
                }}
              />
            )}
            {contributorData?.state === 'unavailable' && (
              <Text>{t`Work-order metrics unavailable for this scope.`}</Text>
            )}
            {contributorData?.state === 'ready' && (
              <>
                <Text size='sm' c='dimmed'>
                  {t`Showing ${offset + (contributorData.records.length ? 1 : 0)}–${offset + contributorData.records.length} of ${contributorData.record_count}`}
                </Text>
                {contributorData.records.length === 0 && (
                  <Text size='sm'>{t`No contributing work matches this selection.`}</Text>
                )}
                {!!contributorData.records.length && (
                  <WorkTableScroll
                    label={t`Contributing work columns`}
                    minWidth={520}
                  >
                    <Table striped>
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>{t`Reference`}</Table.Th>
                          <Table.Th>{t`Machine`}</Table.Th>
                          <Table.Th>{t`State`}</Table.Th>
                          <Table.Th>{t`Due`}</Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {contributorData.records.map((r) => (
                          <Table.Tr key={r.pk}>
                            <Table.Td>
                              <Anchor
                                component={Link}
                                to={machineScopeHref(
                                  `/maintenance/work-orders/${r.pk}/`,
                                  scope
                                )}
                              >
                                {r.label}
                              </Anchor>
                            </Table.Td>
                            <Table.Td>
                              {r.machine_label || (
                                <MissingValue
                                  reason={t`Machine not supplied`}
                                />
                              )}
                            </Table.Td>
                            <Table.Td>
                              {(metricLabels()[r.lifecycle_status ?? ''] ??
                                r.lifecycle_status) || (
                                <MissingValue
                                  reason={t`Work state not supplied`}
                                />
                              )}
                            </Table.Td>
                            <Table.Td>
                              {r.due_date || (
                                <MissingValue
                                  reason={t`Due date not recorded`}
                                />
                              )}
                            </Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                  </WorkTableScroll>
                )}
                <Group>
                  <Button
                    variant='default'
                    disabled={offsets.length === 1}
                    onClick={() => setOffsets((v) => v.slice(0, -1))}
                  >{t`Previous`}</Button>
                  <Button
                    variant='default'
                    disabled={contributorData.next_offset == null}
                    onClick={() =>
                      setOffsets((v) => [...v, contributorData.next_offset!])
                    }
                  >{t`Next`}</Button>
                </Group>
              </>
            )}
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}
