import type { SeriesEntry, SeriesSample } from '@lib/types/MachineHealth';
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
  Stack,
  Table,
  Text,
  useComputedColorScheme,
  useMantineTheme
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
import { formatAge, formatAt, formatDuration, precisionFor } from './format';
import { resolveParameters, withRole } from './resolve';
import { CardGrid, ChartCard } from './sections/common';
import {
  alignRows,
  gapThresholdMs,
  indexSeries,
  newestInstant,
  seriesStats,
  usable
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

/**
 * One precision for a whole column.
 *
 * Adaptive precision per cell is right where a value stands alone, but down a
 * column it gave "0", "0.02" and "-0.0007" three different shapes in three
 * rows, which cannot be compared at a glance. The column asks for what its
 * widest-spread member needs, and every cell prints at that.
 */
function columnPrecision(
  base: number,
  values: (number | null | undefined)[]
): number {
  const numbers = values.filter(
    (v): v is number => typeof v === 'number' && Number.isFinite(v)
  );
  if (numbers.length === 0) {
    return base;
  }
  const range = Math.max(...numbers) - Math.min(...numbers);
  return Math.max(...numbers.map((v) => precisionFor(base, range, v)));
}

/** The per-bay keys a station page compares. */
function bayNumber(bay: string): number {
  return Number((bay.match(/\d+/) ?? ['0'])[0]);
}

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
  const theme = useMantineTheme();
  const scheme = useComputedColorScheme('light');
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
  // The mimic lists bays in key order, which puts P10 before P2; the page
  // reads them the way the plant numbers them.
  const bays = useMemo(
    () =>
      [...(baysQuery.data?.bays ?? [])].sort(
        (a, b) => bayNumber(a.key) - bayNumber(b.key)
      ),
    [baysQuery.data]
  );

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
      // The newest *usable* reading, not merely the newest: a sample carrying
      // the source's over-range marker is bad quality, and every other view
      // of this series already drops it. Summed into a plant total it became
      // the largest number on the page while the chart beside it drew zero.
      const lasts = entries
        .map((e) => [...e.samples].reverse().find(usable))
        .filter((s): s is SeriesSample => !!s);
      if (lasts.length === 0) return;
      const sum = lasts.reduce((acc, s) => acc + (s.v as number), 0);
      // A sum is only as current as its oldest input. Dated by the freshest
      // bay, the tile read "27 s ago" over a total that included a bay which
      // had stopped reporting minutes earlier; the lagging bays stay in the
      // sum - this is the plant total - but the stamp is the weakest one's,
      // and a spread wider than the page's own same-instant tolerance is said.
      const newest = Math.max(...lasts.map((s) => s.t));
      const oldest = Math.min(...lasts.map((s) => s.t));
      const spread = newest - oldest;
      out.push({
        key,
        label,
        value: sum,
        unit: entries[0].unit,
        decimals: 1,
        observedAt: oldest,
        stale: false,
        state: 'unconfigured',
        calculated: true,
        caption:
          spread > gapThresholdMs(window.resolutionSeconds)
            ? t`Calculated: sum of ${lasts.length} of ${bays.length} bays' newest readings, spanning ${formatDuration(spread / 1000)}`
            : t`Calculated: sum of ${lasts.length} of ${bays.length} bays' newest readings`
      });
    };
    total((k) => k.power, t`Plant power`, 'plant-power');
    total((k) => k.flow, t`Plant flow`, 'plant-flow');
    return out;
  }, [parameters, series, bays, byKey, window.resolutionSeconds]);

  const bayLines = (
    pick: (k: ReturnType<typeof bayKeys>) => string,
    hue: string
  ) => {
    // Fourteen bays need fourteen legible colours, and each scheme has only a
    // few shades that clear 3:1; the variety comes from more hues rather than
    // from shades that vanish into the card.
    // Bays are separate machines, not one sensor family, so each wants its
    // own colour. Warm and cyan hues keep only three legible shades on a
    // white card, so fourteen distinct colours need more hues than four.
    const colors = familyColors(
      bays.length,
      [hue, 'cyan', 'grape', 'orange', 'blue', 'violet', 'red'],
      theme,
      scheme
    );
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
    [bays, byKey, window.resolutionSeconds, theme, scheme]
  );
  const flow = useMemo(
    () => bayLines((k) => k.flow, 'teal'),
    [bays, byKey, window.resolutionSeconds, theme, scheme]
  );

  const stationLines = useMemo(() => {
    const forebay = withRole(parameters, 'forebay')[0];
    const surge = withRole(parameters, 'surge_pool')[0];
    const count = withRole(parameters, 'pump_count')[0];
    // The two levels are one cyan family, not two hand-picked shades: a pair
    // picked by hand can land on the same legible shade against the card and
    // draw both levels in one colour.
    const levels = familyColors(2, ['cyan'], theme, scheme);
    const items = [
      forebay && {
        key: 'forebay',
        p: forebay,
        color: levels[0],
        axis: 'left' as const,
        stepped: false
      },
      surge && {
        key: 'surge',
        p: surge,
        color: levels[1],
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
  }, [parameters, series, window.resolutionSeconds, theme, scheme]);

  // The table's own rows: one lookup per bay, and one precision and one unit
  // per column. Power and its window peak share a precision so the two can be
  // read against each other.
  const table = useMemo(() => {
    const last = (key: string) => {
      const entry = byKey.get(key);
      if (!entry?.available) return null;
      // An unusable newest reading is no reading: the row shows a dash rather
      // than the marker's value.
      return [...entry.samples].reverse().find(usable) ?? null;
    };
    const rows = bays.map((bay) => {
      const k = bayKeys(bay.key);
      const st = last(k.status);
      return {
        bay,
        power: last(k.power),
        flow: last(k.flow),
        speed: last(k.speed),
        status: st,
        peak: seriesStats(byKey.get(k.power)),
        units: {
          power: byKey.get(k.power)?.unit ?? '',
          flow: byKey.get(k.flow)?.unit ?? '',
          speed: byKey.get(k.speed)?.unit ?? ''
        }
      };
    });
    const unitOf = (pick: (r: (typeof rows)[number]) => string) =>
      rows.map(pick).find((u) => !!u) ?? '';
    return {
      rows,
      powerDp: columnPrecision(
        2,
        rows.flatMap((r) => [r.power?.v, r.peak?.max])
      ),
      flowDp: columnPrecision(
        1,
        rows.map((r) => r.flow?.v)
      ),
      speedDp: columnPrecision(
        0,
        rows.map((r) => r.speed?.v)
      ),
      powerUnit: unitOf((r) => r.units.power),
      flowUnit: unitOf((r) => r.units.flow),
      speedUnit: unitOf((r) => r.units.speed)
    };
  }, [bays, byKey]);

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
          <CardGrid>
            {stationLines.lines.length > 0 && (
              <ChartCard
                title={t`Forebay level and pumps running`}
                description={
                  stationLines.lines.some((l) => l.yAxisId === 'right') &&
                  stationLines.lines.some((l) => l.yAxisId !== 'right')
                    ? t`Level on the left axis; the running count on the right, as steps.`
                    : undefined
                }
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
          </CardGrid>

          {bays.length > 0 && (
            <Paper withBorder radius='md' p={0} style={{ overflowX: 'auto' }}>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>{t`Bay`}</Table.Th>
                    <Table.Th>{t`State`}</Table.Th>
                    {/* The unit belongs to the column, not to every cell in
                        it; right-aligned, the decimal points line up. */}
                    <Table.Th ta='right'>
                      {t`Active power`}
                      {table.powerUnit ? ` (${table.powerUnit})` : ''}
                    </Table.Th>
                    <Table.Th ta='right'>
                      {t`Discharge flow`}
                      {table.flowUnit ? ` (${table.flowUnit})` : ''}
                    </Table.Th>
                    <Table.Th ta='right'>
                      {t`Shaft speed`}
                      {table.speedUnit ? ` (${table.speedUnit})` : ''}
                    </Table.Th>
                    <Table.Th ta='right'>
                      {t`Window peak power`}
                      {table.powerUnit ? ` (${table.powerUnit})` : ''}
                    </Table.Th>
                    <Table.Th>{t`Newest reading`}</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {table.rows.map((row) => {
                    const { bay, power: p, flow: f, speed: s, peak } = row;
                    const st = row.status;
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
                        <Table.Td ta='right'>
                          {p?.v == null ? '—' : formatAt(p.v, table.powerDp)}
                        </Table.Td>
                        <Table.Td ta='right'>
                          {f?.v == null ? '—' : formatAt(f.v, table.flowDp)}
                        </Table.Td>
                        <Table.Td ta='right'>
                          {s?.v == null ? '—' : formatAt(s.v, table.speedDp)}
                        </Table.Td>
                        <Table.Td ta='right'>
                          {peak ? formatAt(peak.max, table.powerDp) : '—'}
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
