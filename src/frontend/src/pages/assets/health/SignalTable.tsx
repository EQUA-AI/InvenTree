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
  TextInput
} from '@mantine/core';
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react';
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

import { useApi } from '../../../contexts/ApiContext';

import { SignalAnomalyContext, conditionText } from './SignalCondition';
import classes from './SignalTable.module.css';
import { SignalTrendSparkline } from './SignalTrend';
import {
  FreshnessText,
  HealthStateBadge,
  SourceTypeBadge,
  Unit,
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
  return `${value} ${unitLabel(signal.unit)}`;
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
  const [phoneCards, setPhoneCards] = useState(true);
  const now = useNow();
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
          clearable
          value={component}
          onChange={setComponent}
          data={components}
          flex='1 1 220px'
          classNames={{ input: monitoring.input, description: monitoring.meta }}
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
      </Group>
      <MetaText size='xs'>
        {signals.some((signal) => signal.active === false)
          ? t`${visibleSignals.length} of ${signals.length} signal records in this table, including inactive retained bindings. Condition and coverage above describe the whole machine.`
          : t`${visibleSignals.length} of ${signals.length} mapped signals in this table. Condition and coverage above describe the whole machine.`}
      </MetaText>
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
        hiddenFrom='sm'
        w='fit-content'
        aria-pressed={phoneCards}
        onClick={() => setPhoneCards((value) => !value)}
      >
        {phoneCards ? t`All columns table` : t`Signal cards`}
      </Button>
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
                          if ((event.target as HTMLElement).closest('button,a'))
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
                        <MetaText
                          size='xs'
                          title={signal.external_key}
                          truncate='end'
                        >
                          {signal.external_key}
                        </MetaText>
                        {signal.component && (
                          <MetaText size='xs'>
                            {signal.component.name} · {signal.component.code}
                            {signal.component.status !== 'verified'
                              ? ` · ${t`Draft inference`}`
                              : ''}
                          </MetaText>
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
                      {signal.stale && signal.value != null
                        ? t`Last known `
                        : ''}
                      {signal.value == null ? (
                        t`Unavailable · no observation`
                      ) : (
                        <>
                          {typeof signal.value === 'number'
                            ? Number.parseFloat(
                                signal.value.toFixed(3)
                              ).toString()
                            : String(signal.value)}{' '}
                          <Unit code={signal.unit} />
                        </>
                      )}
                    </Text>
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`State`}>
                    <Stack gap={2}>
                      {!signal.stale && (
                        <HealthStateBadge state={signal.state} size='sm' />
                      )}
                      <Text size='xs' lh={1.15}>
                        {conditionText(signal)}
                      </Text>
                    </Stack>
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`Limits`}>
                    <Text size='xs'>
                      {formatSignalLimits(signal) || t`Not configured`}
                    </Text>
                    <MetaText size='xs'>{t`on signal mapping · approval not supplied`}</MetaText>
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`Observed`}>
                    <FreshnessText
                      observedAt={signal.observed_at}
                      stale={signal.stale}
                      thresholdSeconds={signal.freshness_threshold_seconds}
                      now={now}
                    />
                  </Table.Td>
                  {/* biome-ignore lint/a11y/useSemanticElements: Preserve the native role through phone card CSS. */}
                  <Table.Td role='cell' data-label={t`Quality`}>
                    <Text size='sm'>{qualityLabel(signal.quality)}</Text>
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
