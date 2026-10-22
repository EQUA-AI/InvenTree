import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Button,
  Collapse,
  Group,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Tooltip
} from '@mantine/core';
import {
  IconChevronDown,
  IconChevronRight,
  IconFilter,
  IconRefresh
} from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Fragment, useId, useMemo, useState } from 'react';
import monitoring from '../monitoring.module.css';
import { unitLabel } from '../performance/format';
import { useNow } from '../useNow';
import { MetaText } from './common';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  MachineAnomaly,
  MachineSignal,
  SignalTrend
} from '@lib/types/MachineHealth';

import {
  MissingValue,
  ValueTooltip,
  factTooltipProps
} from '../../../components/items/ValueTooltip';
import { useApi } from '../../../contexts/ApiContext';

import { SignalAnomalyContext, conditionText } from './SignalCondition';
import { SignalValue } from './SignalReading';
import classes from './SignalTable.module.css';
import { SignalTrendSparkline } from './SignalTrend';
import { StatusBadge } from './StatusBadge';
import {
  FreshnessText,
  HealthStateBadge,
  SourceTypeBadge,
  formatSignalLimits,
  qualityLabel
} from './common';
import {
  type DisplayClock,
  formatInstant,
  useDisplayClock
} from './mimic/observedTime';
import { signalReading } from './readingPresentation';
import { indexSuppliedTrends, suppliedTrend } from './suppliedTrends';

/**
 * Mapped signals with their current value, provenance and freshness.
 *
 * Last-known readings retain their backend state and an explicit stale label.
 * The compact rows and all-columns view use the same native table and fields.
 */
/** The sparkline window, matched to the one the standalone sparkline uses. */
const SPARKLINE_WINDOW_SECONDS = 10 * 60;

function readingDescription(
  signal: MachineSignal,
  clock: DisplayClock
): string {
  const reading = signalReading(signal);
  return [
    reading.reason ?? reading.text,
    conditionText(signal),
    formatSignalLimits(signal) ?? t`No limits set`,
    formatInstant(signal.observed_at, clock) ?? t`Observation time unavailable`,
    qualityLabel(signal.quality)
  ].join(' · ');
}

export function SignalTable({
  signals,
  machineId,
  selectedBinding,
  onInspect,
  anomalies = null,
  onRefresh,
  preferJustNow = false,
  now: suppliedNow
}: Readonly<{
  signals: MachineSignal[];
  machineId: number;
  anomalies?: MachineAnomaly[] | null;
  selectedBinding?: string | null;
  onInspect?: (binding: number, trigger: HTMLElement) => void;
  onRefresh?: () => void;
  preferJustNow?: boolean;
  now?: number;
}>) {
  const api = useApi();
  const [phoneCards, setPhoneCards] = useState(true);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const pageNow = useNow(1000, suppliedNow == null);
  const now = suppliedNow ?? pageNow;
  const clock = useDisplayClock();
  const instanceId = useId();
  const [order, setOrder] = useState<string>('severity');
  const [search, setSearch] = useState('');
  const [detailsOpen, setDetailsOpen] = useState<Record<number, boolean>>({});
  const [component, setComponent] = useState<string | null>(null);
  const filteredSignals = signals.filter(
    (signal) =>
      (!component || signal.component?.code === component) &&
      (!search.trim() ||
        [
          signal.display_name,
          signal.external_key,
          signal.unit,
          unitLabel(signal.unit),
          signal.component?.name,
          signal.component?.code
        ].some((value) =>
          value?.toLowerCase().includes(search.trim().toLowerCase())
        ))
  );
  const rank = (signal: MachineSignal) =>
    signal.state === 'critical'
      ? 0
      : signal.state === 'warning'
        ? 1
        : signal.state !== 'normal' ||
            signal.stale ||
            signal.value == null ||
            signal.quality !== 'good'
          ? 2
          : 3;
  const visibleSignals = [...filteredSignals].sort(
    (a, b) =>
      (order === 'severity' ? rank(a) - rank(b) : 0) ||
      a.display_name.localeCompare(b.display_name)
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
        <MetaText>{t`No signals are mapped for this machine.`}</MetaText>
      </Paper>
    );
  }

  return (
    <Stack gap='xs'>
      <Group align='flex-end' className={classes.filters}>
        <Group align='flex-end' gap='xs' className={classes.search}>
          <TextInput
            label={t`Search signals`}
            placeholder={t`Search signals`}
            value={search}
            onChange={(event) => setSearch(event.currentTarget.value)}
            flex='1 1 180px'
            classNames={{
              input: monitoring.input,
              wrapper: classes.searchWrapper,
              description: monitoring.meta,
              label: classes.searchLabel
            }}
          />
          <ActionIcon
            hiddenFrom='sm'
            variant='default'
            size={44}
            aria-label={t`Signal filters`}
            aria-expanded={filtersOpen}
            aria-controls={`${instanceId}-filters`}
            onClick={() => setFiltersOpen((open) => !open)}
          >
            <IconFilter size={18} />
          </ActionIcon>
          {onRefresh && (
            <ActionIcon
              hiddenFrom='sm'
              variant='default'
              size={44}
              aria-label={t`Refresh`}
              onClick={onRefresh}
            >
              <IconRefresh size={18} />
            </ActionIcon>
          )}
        </Group>
        <Group
          id={`${instanceId}-filters`}
          align='flex-end'
          gap='sm'
          className={classes.secondaryFilters}
          data-open={filtersOpen}
        >
          <Select
            label={t`Component filter`}
            clearable
            value={component}
            onChange={setComponent}
            data={components}
            flex='1 1 220px'
            classNames={{
              input: monitoring.input,
              description: monitoring.meta
            }}
          />
          <Select
            label={t`Order by`}
            value={order}
            onChange={(value) => setOrder(value ?? 'severity')}
            allowDeselect={false}
            data={[
              { value: 'severity', label: t`Severity` },
              { value: 'name', label: t`Name` }
            ]}
            classNames={{ input: monitoring.input }}
            w={140}
          />
          <Button
            variant='default'
            hiddenFrom='sm'
            aria-pressed={phoneCards}
            onClick={() => setPhoneCards((value) => !value)}
          >
            {phoneCards ? t`All columns table` : t`Signal cards`}
          </Button>
        </Group>
      </Group>
      {selectedBinding &&
        !visibleSignals.some(
          (signal) => String(signal.binding_id) === selectedBinding
        ) && (
          <Text size='sm'>{t`The selected signal is outside this table filter. Its chart and evidence keep their selection.`}</Text>
        )}
      {visibleSignals.length === 0 && (
        <Text size='sm'>{t`No mapped signals match these filters. Clear the filters to see all signals.`}</Text>
      )}
      <Paper
        component='section'
        aria-label={t`Signal readings; scroll for all columns`}
        tabIndex={0}
        withBorder
        p={0}
        style={{ overflowX: 'auto' }}
      >
        {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
        <Table
          role='table'
          aria-label={t`Mapped signal readings`}
          stickyHeader
          striped
          highlightOnHover
          verticalSpacing='xs'
          className={classes.table}
          data-cards={phoneCards}
        >
          {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
          <Table.Thead role='rowgroup'>
            {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
            <Table.Tr role='row'>
              {[
                t`Signal`,
                t`Value`,
                t`State`,
                t`Limits`,
                t`Observed`,
                t`Quality`,
                t`Trend`
              ].map((label) => (
                // biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS.
                <Table.Th role='columnheader' key={label} scope='col'>
                  {label}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
          <Table.Tbody role='rowgroup'>
            {visibleSignals.map((signal) => (
              <Fragment key={signal.binding_id}>
                {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                <Table.Tr
                  role='row'
                  data-selected={selectedBinding === String(signal.binding_id)}
                  data-details-open={!!detailsOpen[signal.binding_id]}
                  aria-current={
                    selectedBinding === String(signal.binding_id)
                      ? 'true'
                      : undefined
                  }
                  bg={
                    selectedBinding === String(signal.binding_id)
                      ? 'var(--mantine-primary-color-light)'
                      : undefined
                  }
                  onClick={
                    onInspect
                      ? (event) => {
                          if (
                            (event.target as HTMLElement).closest(
                              'button,a,[data-signal-tooltip]'
                            )
                          )
                            return;
                          const trigger =
                            event.currentTarget.querySelector('button');
                          if (trigger) onInspect(signal.binding_id, trigger);
                        }
                      : undefined
                  }
                >
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`Signal`}>
                    <Group gap={4} wrap='nowrap' align='flex-start'>
                      <Tooltip
                        label={readingDescription(signal, clock)}
                        {...factTooltipProps}
                      >
                        <ActionIcon
                          variant='subtle'
                          className={monitoring.link}
                          aria-label={t`Details for ${signal.display_name}`}
                          aria-expanded={!!detailsOpen[signal.binding_id]}
                          aria-controls={`${instanceId}-${signal.binding_id}`}
                          onClick={() =>
                            setDetailsOpen((open) => ({
                              ...open,
                              [signal.binding_id]: !open[signal.binding_id]
                            }))
                          }
                        >
                          {detailsOpen[signal.binding_id] ? (
                            <IconChevronDown size={16} />
                          ) : (
                            <IconChevronRight size={16} />
                          )}
                        </ActionIcon>
                      </Tooltip>
                      <Stack gap={0} miw={0}>
                        {onInspect ? (
                          <Button
                            className={monitoring.link}
                            justify='flex-start'
                            variant='subtle'
                            size='compact-sm'
                            px={0}
                            h='auto'
                            styles={{
                              inner: { height: 'auto' },
                              label: {
                                height: 'auto',
                                whiteSpace: 'normal',
                                textAlign: 'left'
                              }
                            }}
                            aria-label={t`Inspect signal: ${signal.display_name}`}
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
                          <Text size='xs' fw={600}>
                            {signal.critical_designation.review_required ||
                            signal.critical_designation.applies_current ===
                              false
                              ? t`Critical signal · review required`
                              : t`Designated critical signal`}
                          </Text>
                        )}
                        {signal.active === false && (
                          <MetaText size='xs'>{t`Inactive binding — retained for critical coverage review`}</MetaText>
                        )}
                      </Stack>
                    </Group>
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td
                    role='cell'
                    data-label={t`Value`}
                    style={{
                      textAlign: 'right',
                      fontVariantNumeric: 'tabular-nums'
                    }}
                  >
                    <Text
                      size='sm'
                      fw={signal.stale ? 400 : 500}
                      className={signal.stale ? monitoring.meta : undefined}
                    >
                      <SignalValue signal={signal} focusable={true} />
                    </Text>
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`State`}>
                    {signal.stale && signal.state !== 'offline' ? (
                      <ValueTooltip
                        label={conditionText(signal)}
                        focusable={true}
                      >
                        <StatusBadge
                          tone='stale'
                          size='sm'
                        >{t`Stale`}</StatusBadge>
                      </ValueTooltip>
                    ) : signal.state === 'unknown' ? (
                      <MissingValue
                        label={t`State`}
                        reason={conditionText(signal)}
                        focusable={true}
                      />
                    ) : (
                      <ValueTooltip
                        label={conditionText(signal)}
                        focusable={false}
                      >
                        <HealthStateBadge state={signal.state} size='sm' />
                      </ValueTooltip>
                    )}
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`Limits`}>
                    {formatSignalLimits(signal) ? (
                      <ValueTooltip
                        focusable={false}
                        label={`${formatSignalLimits(signal)} · ${t`Limits on signal mapping; engineering approval not supplied`}`}
                      >
                        <Text component='span' size='xs'>
                          {formatSignalLimits(signal)}
                        </Text>
                      </ValueTooltip>
                    ) : (
                      <MissingValue
                        label={t`Limits`}
                        reason={`${t`No limits set`} · ${t`Limits on signal mapping; engineering approval not supplied`}`}
                        focusable={true}
                      />
                    )}
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`Observed`}>
                    <FreshnessText
                      observedAt={signal.observed_at}
                      stale={signal.stale}
                      thresholdSeconds={signal.freshness_threshold_seconds}
                      now={now}
                      focusable={false}
                      preferJustNow={preferJustNow}
                    />
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`Quality`}>
                    {signal.quality === 'unknown' ? (
                      <MissingValue
                        label={t`Quality`}
                        reason={t`Quality unavailable`}
                        focusable={true}
                      />
                    ) : (
                      <Text size='sm'>{qualityLabel(signal.quality)}</Text>
                    )}
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`Trend`}>
                    <SignalTrendSparkline
                      machineId={machineId}
                      bindingId={signal.binding_id}
                      trend={suppliedTrend(trendByBinding, signal.binding_id)}
                      supplierPending={trendsQuery.isLoading}
                      supplierFailed={trendsQuery.isError}
                    />
                  </Table.Td>
                </Table.Tr>
                {detailsOpen[signal.binding_id] && (
                  // biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS.
                  <Table.Tr role='row' className={classes.details}>
                    {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                    <Table.Td role='cell' colSpan={7}>
                      <Collapse
                        expanded
                        id={`${instanceId}-${signal.binding_id}`}
                      >
                        <Group gap='lg' align='flex-start'>
                          <Stack gap={2}>
                            <Text
                              size='sm'
                              fw={500}
                            >{t`Active anomalies`}</Text>
                            <SignalAnomalyContext
                              signal={signal}
                              anomalies={anomalies}
                            />
                          </Stack>
                          <Stack gap={2}>
                            <Text size='sm' fw={500}>{t`Source`}</Text>
                            <Group gap={6}>
                              <SourceTypeBadge type={signal.source_type} />
                              <Text size='sm'>{signal.source_name}</Text>
                            </Group>
                            <MetaText
                              size='xs'
                              style={{ overflowWrap: 'anywhere' }}
                            >
                              {signal.external_key}
                            </MetaText>
                            <MetaText size='xs'>{signal.signal_kind}</MetaText>
                          </Stack>
                          {signal.component && (
                            <Stack gap={2}>
                              <Text size='sm' fw={500}>{t`Component`}</Text>
                              <Text size='sm'>
                                {signal.component.name} ·{' '}
                                {signal.component.code}
                              </Text>
                              {signal.component.status !== 'verified' && (
                                <MetaText size='xs'>{t`Draft inference`}</MetaText>
                              )}
                            </Stack>
                          )}
                        </Group>
                      </Collapse>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Fragment>
            ))}
          </Table.Tbody>
        </Table>
      </Paper>
    </Stack>
  );
}
export default SignalTable;
