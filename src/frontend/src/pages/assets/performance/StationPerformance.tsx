import type { SeriesEntry } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Center,
  Group,
  Loader,
  LoadingOverlay,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Link } from 'react-router-dom';

import { useApi } from '../../../contexts/ApiContext';
import type { MimicData } from '../health/PumphouseMimic';
import { KpiStrip, type KpiTile } from './KpiStrip';
import type { PerformanceMachine } from './PerformancePanel';
import { useClock, useWindowState, windowInfoFrom } from './PerformancePanel';
import { LiveIndicator, TimeRangeControl } from './TimeRangeControl';
import { TrendChart, type TrendSeries, familyColors } from './TrendChart';
import { formatAge, formatValue } from './format';
import { resolveParameters, withRole } from './resolve';
import { ChartCard } from './sections/common';
import {
  alignRows,
  gapThresholdMs,
  indexSeries,
  newestInstant,
  seriesStats
} from './series';
import {
  useDataRange,
  useMachineSeries,
  useMachineSignals
} from './usePerformanceData';

const STATION_KEYS = [
  '/dex/COMMAN_FORBAY_LEVEL',
  '/dex/SURGEPOOL_LEVEL',
  '/pc',
  '/st'
];

/** The per-bay keys a station page compares. */
function bayKeys(bay: string): {
  power: string;
  flow: string;
  status: string;
  speed: string;
} {
  const number = bay.replace(/^P/, '');
  return {
    power: `/dex/PUMP${number}_ACTIVE_POWER`,
    flow: `/pd/${bay}/dv`,
    status: `/pd/${bay}/st`,
    speed: `/dex/PUMP${number}_SPEED`
  };
}

/**
 * Performance for a whole station: the forebay it draws from, how many bays
 * are running, and how the bays compare. Per-bay values are read through the
 * same series endpoint as everything else - the station's scope includes its
 * bays - so the page costs one history read however many pumps it has.
 */
export function StationPerformance({
  station
}: Readonly<{ station: PerformanceMachine }>) {
  const api = useApi();
  const now = useClock();
  const range = useWindowState();
  const signalsQuery = useMachineSignals(station.pk, range.live);
  const dataRange = useDataRange(station.pk);

  // The bay list, read once; the mimic already knows which slots exist.
  const baysQuery = useQuery<MimicData>({
    queryKey: ['station-bays', station.pk],
    staleTime: 10 * 60 * 1000,
    queryFn: async () =>
      (
        await api.get(`/api/machine-health/station/${station.pk}/mimic/`, {
          timeout: 15000
        })
      ).data
  });
  const bays = useMemo(() => baysQuery.data?.bays ?? [], [baysQuery.data]);

  const keys = useMemo(
    () => [
      ...STATION_KEYS,
      ...bays.flatMap((bay) => Object.values(bayKeys(bay.key)))
    ],
    [bays]
  );
  const targets = useMemo(() => ({ keys }), [keys]);
  const seriesQuery = useMachineSeries(
    station.pk,
    targets,
    range.window,
    range.live
  );
  const response = seriesQuery.data;
  const series = useMemo(() => indexSeries(response), [response]);
  const byKey = useMemo(() => {
    const map = new Map<string, SeriesEntry>();
    for (const entry of response?.series ?? [])
      map.set(entry.external_key, entry);
    return map;
  }, [response]);
  const window = useMemo(
    () => windowInfoFrom(response, range.window, now),
    [response, range.window, now]
  );
  const parameters = useMemo(
    () => resolveParameters(signalsQuery.data ?? []),
    [signalsQuery.data]
  );
  const newestAt = useMemo(() => newestInstant([...series.values()]), [series]);
  const syncId = `performance-${station.pk}`;

  const tiles = useMemo<KpiTile[]>(() => {
    const out: KpiTile[] = [];
    const forebay = withRole(parameters, 'forebay')[0];
    if (forebay) {
      out.push({
        key: 'forebay',
        label: forebay.label,
        value:
          typeof forebay.signal.value === 'number'
            ? forebay.signal.value
            : null,
        unit: forebay.signal.unit,
        decimals: forebay.definition.decimals,
        observedAt: forebay.signal.observed_at
          ? new Date(forebay.signal.observed_at).getTime()
          : null,
        stale: forebay.signal.stale,
        state: 'unconfigured',
        series: series.get(forebay.signal.binding_id)
      });
    }
    const count = withRole(parameters, 'pump_count')[0];
    if (count) {
      out.push({
        key: 'pumps-running',
        label: t`Pumps running`,
        value:
          typeof count.signal.value === 'number' ? count.signal.value : null,
        unit: '',
        decimals: 0,
        observedAt: count.signal.observed_at
          ? new Date(count.signal.observed_at).getTime()
          : null,
        stale: count.signal.stale,
        state: 'unconfigured',
        series: series.get(count.signal.binding_id),
        caption:
          bays.length > 0 ? t`of ${bays.length} registered bays` : undefined
      });
    }
    // Plant totals are sums of the bays' newest readings: calculated, and said so.
    const total = (
      pick: (k: ReturnType<typeof bayKeys>) => string,
      label: string,
      key: string
    ) => {
      const entries = bays
        .map((bay) => byKey.get(pick(bayKeys(bay.key))))
        .filter((e): e is SeriesEntry => !!e?.available);
      const lasts = entries
        .map((e) => e.samples[e.samples.length - 1])
        .filter((s) => s && s.v !== null);
      if (lasts.length === 0) return;
      const sum = lasts.reduce((acc, s) => acc + (s.v as number), 0);
      out.push({
        key,
        label,
        value: sum,
        unit: entries[0].unit,
        decimals: 1,
        observedAt: Math.max(...lasts.map((s) => s.t)),
        stale: false,
        state: 'unconfigured',
        caption: t`Calculated: sum of ${lasts.length} of ${bays.length} bays' newest readings`
      });
    };
    total((k) => k.power, t`Plant power`, 'plant-power');
    total((k) => k.flow, t`Plant flow`, 'plant-flow');
    return out;
  }, [parameters, series, bays, byKey]);

  const bayLines = (
    pick: (k: ReturnType<typeof bayKeys>) => string,
    hue: string
  ) => {
    const colors = familyColors(bays.length, [hue, 'cyan']);
    const items = bays.map((bay, i) => ({
      key: bay.key,
      label: bay.name.split('/').pop()?.trim() || bay.key,
      entry: byKey.get(pick(bayKeys(bay.key))),
      color: colors[i]
    }));
    const present = items.filter((item) => item.entry?.available);
    const unit = present[0]?.entry?.unit ?? '';
    const lines: TrendSeries[] = present.map((item) => ({
      key: item.key,
      label: item.label,
      color: item.color,
      unit,
      decimals: 2
    }));
    const rows = alignRows(
      present.map((item) => ({ key: item.key, entry: item.entry })),
      gapThresholdMs(window.resolutionSeconds)
    );
    return { lines, rows, unit };
  };

  const power = useMemo(
    () => bayLines((k) => k.power, 'blue'),
    [bays, byKey, window.resolutionSeconds]
  );
  const flow = useMemo(
    () => bayLines((k) => k.flow, 'teal'),
    [bays, byKey, window.resolutionSeconds]
  );

  const stationLines = useMemo(() => {
    const forebay = withRole(parameters, 'forebay')[0];
    const surge = withRole(parameters, 'surge_pool')[0];
    const count = withRole(parameters, 'pump_count')[0];
    const items = [
      forebay && {
        key: 'forebay',
        p: forebay,
        color: 'cyan.7',
        axis: 'left' as const,
        stepped: false
      },
      surge && {
        key: 'surge',
        p: surge,
        color: 'cyan.4',
        axis: 'left' as const,
        stepped: false
      },
      count && {
        key: 'count',
        p: count,
        color: 'green.7',
        axis: 'right' as const,
        stepped: true
      }
    ].filter(Boolean) as {
      key: string;
      p: (typeof parameters)[number];
      color: string;
      axis: 'left' | 'right';
      stepped: boolean;
    }[];
    const lines: TrendSeries[] = items.map((item) => ({
      key: item.key,
      label: item.p.label,
      color: item.color,
      unit: item.p.signal.unit,
      decimals: item.p.definition.decimals,
      yAxisId: item.axis,
      stepped: item.stepped
    }));
    const rows = alignRows(
      items.map((item) => ({
        key: item.key,
        entry: series.get(item.p.signal.binding_id)
      })),
      gapThresholdMs(window.resolutionSeconds)
    );
    return {
      lines,
      rows,
      unit: forebay?.signal.unit ?? surge?.signal.unit ?? ''
    };
  }, [parameters, series, window.resolutionSeconds]);

  if (signalsQuery.isLoading || baysQuery.isLoading) {
    return (
      <Center p='xl'>
        <Loader />
      </Center>
    );
  }
  if (signalsQuery.isError) {
    return (
      <Alert
        color='red'
        variant='light'
        title={t`Performance data unavailable`}
      >
        {t`The health service could not be reached.`}
        <Group mt='xs'>
          <Button
            size='xs'
            variant='light'
            onClick={() => signalsQuery.refetch()}
          >
            {t`Retry`}
          </Button>
        </Group>
      </Alert>
    );
  }

  const freshness = signalsQuery.data?.[0]?.freshness_threshold_seconds ?? null;

  return (
    <Stack gap='lg'>
      <Group justify='space-between' align='flex-start' wrap='wrap' gap='md'>
        <Stack gap={4} style={{ flex: 1, minWidth: 320 }}>
          <TimeRangeControl
            preset={range.preset}
            onPreset={range.onPreset}
            onCustom={range.onCustom}
            zoomed={range.zoom}
            onClearZoom={range.onClearZoom}
            live={range.live}
            onLive={range.onLive}
            onRefresh={() => {
              signalsQuery.refetch();
              seriesQuery.refetch();
            }}
            refreshing={seriesQuery.isFetching}
            dataRange={dataRange.data}
          />
        </Stack>
        <LiveIndicator
          live={range.live}
          fetching={seriesQuery.isFetching}
          newestAt={newestAt}
          updatedAt={seriesQuery.dataUpdatedAt || null}
          response={response}
          freshnessSeconds={freshness}
          now={now}
        />
      </Group>

      <KpiStrip tiles={tiles} now={now} />

      {seriesQuery.isError && (
        <Alert
          color='red'
          variant='light'
          title={t`Could not read history for this window`}
        >
          {t`The request failed; the charts below show nothing rather than a partial line.`}
        </Alert>
      )}

      <div style={{ position: 'relative' }}>
        <LoadingOverlay
          visible={seriesQuery.isLoading}
          zIndex={5}
          overlayProps={{ blur: 1 }}
        />
        <Stack gap='md'>
          <SimpleGrid cols={{ base: 1, lg: 2 }} spacing='md'>
            {stationLines.lines.length > 0 && (
              <ChartCard
                title={t`Forebay level and pumps running`}
                description={t`Level on the left axis; the running count on the right, as steps.`}
              >
                <TrendChart
                  rows={stationLines.rows}
                  series={stationLines.lines}
                  windowStart={window.start}
                  windowEnd={window.end}
                  windowSeconds={window.seconds}
                  unit={stationLines.unit}
                  rightUnit=''
                  syncId={syncId}
                  onZoom={range.onZoom}
                />
              </ChartCard>
            )}
            {power.lines.length > 0 && (
              <ChartCard title={t`Active power by bay`}>
                <TrendChart
                  rows={power.rows}
                  series={power.lines}
                  windowStart={window.start}
                  windowEnd={window.end}
                  windowSeconds={window.seconds}
                  unit={power.unit}
                  syncId={syncId}
                  toggleable
                  onZoom={range.onZoom}
                />
              </ChartCard>
            )}
            {flow.lines.length > 0 && (
              <ChartCard title={t`Discharge flow by bay`}>
                <TrendChart
                  rows={flow.rows}
                  series={flow.lines}
                  windowStart={window.start}
                  windowEnd={window.end}
                  windowSeconds={window.seconds}
                  unit={flow.unit}
                  syncId={syncId}
                  toggleable
                  onZoom={range.onZoom}
                />
              </ChartCard>
            )}
          </SimpleGrid>

          {bays.length > 0 && (
            <Paper withBorder radius='md' p={0} style={{ overflowX: 'auto' }}>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t`Bay`}</Table.Th>
                    <Table.Th>{t`State`}</Table.Th>
                    <Table.Th>{t`Active power`}</Table.Th>
                    <Table.Th>{t`Discharge flow`}</Table.Th>
                    <Table.Th>{t`Shaft speed`}</Table.Th>
                    <Table.Th>{t`Window peak power`}</Table.Th>
                    <Table.Th>{t`Newest reading`}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {bays.map((bay) => {
                    const k = bayKeys(bay.key);
                    const last = (key: string) => {
                      const entry = byKey.get(key);
                      if (!entry?.available) return null;
                      return entry.samples[entry.samples.length - 1] ?? null;
                    };
                    const p = last(k.power);
                    const f = last(k.flow);
                    const s = last(k.speed);
                    const st = last(k.status);
                    const peak = seriesStats(byKey.get(k.power));
                    const newest = Math.max(
                      ...[p, f, s, st].map((x) => x?.t ?? 0)
                    );
                    const state =
                      st?.v === null || st?.v === undefined
                        ? bay.state
                        : st.v > 0
                          ? 'running'
                          : st.v < 0
                            ? 'fault'
                            : 'idle';
                    return (
                      <Table.Tr key={bay.key}>
                        <Table.Td>
                          <Anchor
                            component={Link}
                            to={`/machines/machine/${bay.machine}/performance`}
                            size='sm'
                          >
                            {bay.name.split('/').pop()?.trim() || bay.key}
                          </Anchor>
                          {!bay.active && (
                            <Text size='xs' c='dimmed'>
                              {t`Inactive equipment`}
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td>
                          <Badge
                            size='sm'
                            variant='light'
                            color={
                              state === 'running'
                                ? 'green'
                                : state === 'fault'
                                  ? 'red'
                                  : state === 'idle'
                                    ? 'yellow'
                                    : 'gray'
                            }
                          >
                            {state === 'running'
                              ? t`Running`
                              : state === 'fault'
                                ? t`Fault`
                                : state === 'idle'
                                  ? t`Idle`
                                  : t`Unknown`}
                          </Badge>
                        </Table.Td>
                        <Table.Td>
                          {formatValue(
                            p?.v ?? null,
                            2,
                            byKey.get(k.power)?.unit
                          )}
                        </Table.Td>
                        <Table.Td>
                          {formatValue(
                            f?.v ?? null,
                            1,
                            byKey.get(k.flow)?.unit
                          )}
                        </Table.Td>
                        <Table.Td>
                          {formatValue(
                            s?.v ?? null,
                            0,
                            byKey.get(k.speed)?.unit
                          )}
                        </Table.Td>
                        <Table.Td>
                          {peak
                            ? formatValue(peak.max, 2, byKey.get(k.power)?.unit)
                            : '—'}
                        </Table.Td>
                        <Table.Td>
                          <Text size='xs' c='dimmed'>
                            {newest > 0
                              ? formatAge(newest, now)
                              : t`No reading`}
                          </Text>
                        </Table.Td>
                      </Table.Tr>
                    );
                  })}
                </Table.Tbody>
              </Table>
            </Paper>
          )}
        </Stack>
      </div>
    </Stack>
  );
}

export default StationPerformance;
