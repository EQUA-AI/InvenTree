import { t } from '@lingui/core/macro';
import {
  Button,
  Group,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import monitoring from '../monitoring.module.css';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  MachineAnomaly,
  MachineSignal,
  SignalTrend
} from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';

import { SignalAnomalyContext, conditionText } from './SignalCondition';
import classes from './SignalTable.module.css';
import { SignalTrendSparkline } from './SignalTrend';
import {
  HealthStateBadge,
  ObservedAt,
  SourceTypeBadge,
  formatSignalLimits,
  qualityLabel
} from './common';
import { indexSuppliedTrends, suppliedTrend } from './suppliedTrends';

function formatValue(signal: MachineSignal): string {
  if (signal.value === null || signal.value === undefined) {
    return t`Unavailable · no observation`;
  }
  const value =
    typeof signal.value === 'number'
      ? Number.parseFloat(signal.value.toFixed(3)).toString()
      : String(signal.value);
  return signal.unit ? `${value} ${signal.unit}` : value;
}

/**
 * Mapped signals with their current value, provenance and freshness.
 *
 * Last-known readings retain their backend state and an explicit stale label.
 * The compact rows and all-columns view use the same native table and fields.
 */
/** The sparkline window, matched to the one the standalone sparkline uses. */
const SPARKLINE_WINDOW_SECONDS = 10 * 60;

export function SignalTable({
  signals,
  machineId,
  selectedBinding,
  onInspect,
  anomalies = null
}: Readonly<{
  signals: MachineSignal[];
  machineId: number;
  anomalies?: MachineAnomaly[] | null;
  selectedBinding?: string | null;
  onInspect?: (binding: number, trigger: HTMLElement) => void;
}>) {
  const api = useApi();
  const [mobileCards, setMobileCards] = useState(true);
  const [search, setSearch] = useState('');
  const [detailsOpen, setDetailsOpen] = useState<Record<number, boolean>>({});
  const [component, setComponent] = useState<string | null>(null);
  const visibleSignals = signals.filter(
    (signal) =>
      (!component || signal.component?.code === component) &&
      (!search.trim() ||
        [
          signal.display_name,
          signal.external_key,
          signal.unit,
          signal.component?.name,
          signal.component?.code
        ].some((value) =>
          value?.toLowerCase().includes(search.trim().toLowerCase())
        ))
  );
  const components = [
    ...new Map(
      signals.flatMap((signal) =>
        signal.component
          ? [
              [
                signal.component.code,
                {
                  value: signal.component.code,
                  label: `${signal.component.name} · ${signal.component.code}`
                }
              ] as const
            ]
          : []
      )
    ).values()
  ];

  const bindingIds = useMemo(
    () =>
      signals
        .filter((signal) => signal.active !== false)
        .map((signal) => signal.binding_id)
        .filter(Boolean),
    [signals]
  );

  // One federated read for the whole table rather than one per row. Each
  // sparkline used to fetch its own window, and because a snapshot is a
  // whole-station document the source then read and parsed the same documents
  // once per row - about thirty times on a pump, seventy on some. Measured at
  // 6.4s per row against 6.7s for the entire table.
  const trendsQuery = useQuery<{ results: SignalTrend[] }>({
    queryKey: ['machine-health-trends', machineId, bindingIds],
    enabled: bindingIds.length > 0,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const end = new Date();
      const start = new Date(end.getTime() - SPARKLINE_WINDOW_SECONDS * 1000);
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_trends, machineId),
        {
          params: {
            bindings: bindingIds.join(','),
            from: start.toISOString(),
            to: end.toISOString()
          },
          // The global default is 5s, which a federated read of a historian
          // window does not fit in - measured at about 5s server-side for a
          // wide table before the network. Left at the default, every one of
          // these would time out and then retry, which is what made this page
          // fail rather than merely be slow.
          timeout: 60 * 1000
        }
      );
      return response.data;
    }
  });

  const trendByBinding = useMemo(
    () => indexSuppliedTrends(trendsQuery.data?.results),
    [trendsQuery.data]
  );

  if (signals.length === 0) {
    return (
      <Paper withBorder p='md'>
        <Text
          className={monitoring.meta}
        >{t`No signals are mapped for this machine.`}</Text>
      </Paper>
    );
  }

  return (
    <Stack gap='xs'>
      <Group align='flex-end'>
        <TextInput
          label={t`Search signals`}
          placeholder={t`Signal, tag, component or unit`}
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
          flex='1 1 220px'
          classNames={{ input: monitoring.input, description: monitoring.meta }}
        />
        <Select
          label={t`Component filter`}
          description={t`Clear to include all components`}
          clearable
          value={component}
          onChange={setComponent}
          data={components}
          flex='1 1 220px'
          classNames={{ input: monitoring.input, description: monitoring.meta }}
        />
      </Group>
      <Text size='xs' className={monitoring.meta}>
        {signals.some((signal) => signal.active === false)
          ? t`${visibleSignals.length} of ${signals.length} signal records in this table, including inactive retained bindings. Condition and coverage above describe the whole machine.`
          : t`${visibleSignals.length} of ${signals.length} mapped signals in this table. Condition and coverage above describe the whole machine.`}
      </Text>
      {selectedBinding &&
        !visibleSignals.some(
          (signal) => String(signal.binding_id) === selectedBinding
        ) && (
          <Text size='sm'>{t`The selected signal is outside this table filter. Its chart and evidence keep their selection.`}</Text>
        )}
      {visibleSignals.length === 0 && (
        <Text size='sm'>{t`No mapped signals match these filters. Clear the filters to see all signals.`}</Text>
      )}
      <Button
        variant='default'
        onClick={() => setMobileCards((value) => !value)}
      >
        {mobileCards ? t`All columns table` : t`Signal cards`}
      </Button>
      <Paper
        component='section'
        aria-label={t`Signal readings; scroll for all columns`}
        tabIndex={0}
        withBorder
        p={0}
        style={{ overflowX: 'auto' }}
      >
        {/* biome-ignore lint/a11y/useSemanticElements: Mantine renders native table elements; explicit roles preserve them when phone CSS changes display. */}
        <Table
          role='table'
          aria-label={t`Mapped signal readings`}
          striped
          highlightOnHover
          className={classes.table}
          data-cards={mobileCards}
        >
          {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
          <Table.Thead role='rowgroup'>
            {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
            <Table.Tr role='row'>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th role='columnheader' scope='col'>{t`Signal`}</Table.Th>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th role='columnheader' scope='col'>{t`Value`}</Table.Th>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th role='columnheader' scope='col'>{t`State`}</Table.Th>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th role='columnheader' scope='col'>{t`Trend`}</Table.Th>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th role='columnheader' scope='col'>{t`Observed`}</Table.Th>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th
                role='columnheader'
                scope='col'
              >{t`Current condition`}</Table.Th>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th
                role='columnheader'
                scope='col'
              >{t`Active anomalies`}</Table.Th>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th role='columnheader' scope='col'>{t`Quality`}</Table.Th>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th role='columnheader' scope='col'>{t`Source`}</Table.Th>
              {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
              <Table.Th role='columnheader' scope='col'>{t`Limits`}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
          <Table.Tbody role='rowgroup'>
            {visibleSignals.map((signal) => (
              // biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout.
              <Table.Tr
                role='row'
                data-selected={selectedBinding === String(signal.binding_id)}
                data-details-open={!!detailsOpen[signal.binding_id]}
                key={signal.binding_id}
                bg={
                  selectedBinding === String(signal.binding_id)
                    ? 'var(--mantine-color-body)'
                    : undefined
                }
                onClick={
                  onInspect
                    ? (event) => {
                        if ((event.target as HTMLElement).closest('button, a'))
                          return;
                        const trigger =
                          event.currentTarget.querySelector('button');
                        if (trigger) onInspect(signal.binding_id, trigger);
                      }
                    : undefined
                }
              >
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td role='cell' data-label={t`Signal`}>
                  <Stack gap={0}>
                    {onInspect ? (
                      <Button
                        className={monitoring.link}
                        justify='flex-start'
                        variant='subtle'
                        size='compact-sm'
                        px={0}
                        h='auto'
                        style={{ whiteSpace: 'normal', textAlign: 'left' }}
                        aria-label={t`Inspect signal: ${signal.display_name}`}
                        aria-controls='health-signal-evidence'
                        aria-pressed={
                          selectedBinding === String(signal.binding_id)
                        }
                        onClick={(event) =>
                          onInspect(signal.binding_id, event.currentTarget)
                        }
                      >
                        {signal.display_name}
                      </Button>
                    ) : (
                      <Text size='sm'>{signal.display_name}</Text>
                    )}
                    {signal.critical_designation?.critical && (
                      <Text size='sm' fw={600}>
                        {signal.critical_designation.review_required ||
                        signal.critical_designation.applies_current === false
                          ? t`Critical signal · review required`
                          : t`Designated critical signal`}
                      </Text>
                    )}
                    {signal.component && (
                      <Text size='xs' className={monitoring.meta}>
                        {signal.component.name} · {signal.component.code}
                        {signal.component.status !== 'verified'
                          ? ` · ${t`Draft inference`}`
                          : ''}
                      </Text>
                    )}
                    {signal.active === false && (
                      <Text
                        size='xs'
                        className={monitoring.meta}
                      >{t`Inactive binding — retained for critical coverage review`}</Text>
                    )}
                    {signal.signal_kind && (
                      <Text size='xs' className={monitoring.meta}>
                        {signal.signal_kind}
                      </Text>
                    )}
                  </Stack>
                </Table.Td>
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td role='cell' data-label={t`Value`}>
                  <Text size='sm' fw={500}>
                    {formatValue(signal)}
                  </Text>
                  {signal.stale && signal.value != null && (
                    <Text
                      size='sm'
                      className={monitoring.meta}
                    >{t`Last known · not current`}</Text>
                  )}
                </Table.Td>
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td role='cell' data-label={t`State`}>
                  <HealthStateBadge state={signal.state} size='sm' />
                </Table.Td>
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td role='cell' data-label={t`Trend`}>
                  <SignalTrendSparkline
                    machineId={machineId}
                    bindingId={signal.binding_id}
                    // Missing/pending supplied rows stay null, suppressing N+1 reads.
                    trend={suppliedTrend(trendByBinding, signal.binding_id)}
                    supplierPending={trendsQuery.isLoading}
                    supplierFailed={trendsQuery.isError}
                  />
                </Table.Td>
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td role='cell' data-label={t`Observed`}>
                  <ObservedAt
                    observedAt={signal.observed_at}
                    stale={signal.stale}
                  />
                </Table.Td>
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td role='cell' data-label={t`Current condition`}>
                  <Text size='sm'>{conditionText(signal)}</Text>
                  {mobileCards && (
                    <Button
                      className={monitoring.link}
                      variant='subtle'
                      size='compact-sm'
                      mt='xs'
                      aria-label={t`Details for ${signal.display_name}`}
                      aria-expanded={!!detailsOpen[signal.binding_id]}
                      aria-controls={[
                        'anomalies',
                        'quality',
                        'source',
                        'limits'
                      ]
                        .map(
                          (detail) => `signal-${signal.binding_id}-${detail}`
                        )
                        .join(' ')}
                      onClick={() =>
                        setDetailsOpen((open) => ({
                          ...open,
                          [signal.binding_id]: !open[signal.binding_id]
                        }))
                      }
                    >
                      {detailsOpen[signal.binding_id]
                        ? t`Hide details`
                        : t`Show details`}
                    </Button>
                  )}
                </Table.Td>
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td
                  role='cell'
                  data-detail='true'
                  id={`signal-${signal.binding_id}-anomalies`}
                  data-label={t`Active anomalies`}
                >
                  <SignalAnomalyContext signal={signal} anomalies={anomalies} />
                </Table.Td>
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td
                  role='cell'
                  data-detail='true'
                  id={`signal-${signal.binding_id}-quality`}
                  data-label={t`Quality`}
                >
                  <Text size='sm'>{qualityLabel(signal.quality)}</Text>
                </Table.Td>
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td
                  role='cell'
                  data-detail='true'
                  id={`signal-${signal.binding_id}-source`}
                  data-label={t`Source`}
                >
                  <Group gap={6} wrap='wrap'>
                    <SourceTypeBadge type={signal.source_type} />
                    <Text size='xs'>{signal.source_name}</Text>
                  </Group>
                  <Text
                    size='xs'
                    ff='monospace'
                    style={{ overflowWrap: 'anywhere' }}
                  >
                    {signal.external_key}
                  </Text>
                </Table.Td>
                {/* biome-ignore lint/a11y/useSemanticElements: Explicit native role survives the phone CSS layout. */}
                <Table.Td
                  role='cell'
                  data-detail='true'
                  id={`signal-${signal.binding_id}-limits`}
                  data-label={t`Limits`}
                >
                  <Text size='sm'>
                    {formatSignalLimits(signal) || t`Not configured`}
                  </Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Paper>
    </Stack>
  );
}

export default SignalTable;
