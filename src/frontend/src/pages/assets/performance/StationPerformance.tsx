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
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { HistoryProvenance } from '../investigation/HistoryProvenance';
import { InvestigationSlices } from '../investigation/InvestigationSlices';
import { LinkedEvidence } from '../investigation/LinkedEvidence';
import { displayLinkedWindow } from '../investigation/windows';
import { HistoryReadStack } from './HistoryReadContext';
import { failedHistoryTargets } from './historyBatches';

import { useApi } from '../../../contexts/ApiContext';
import type { MimicData } from '../health/PumphouseMimic';
import { KpiStrip, type KpiTile } from './KpiStrip';
import type { PerformanceMachine } from './PerformancePanel';
import { useClock, useWindowState, windowInfoFrom } from './PerformancePanel';
import { LiveIndicator, TimeRangeControl } from './TimeRangeControl';
import { TrendChart, type TrendSeries, familyColors } from './TrendChart';
import { WindowStatus, clockLabel, formatWindow } from './WindowStatus';
import { formatAge, formatAt, formatDuration, precisionFor } from './format';
import { resolveParameters, withRole } from './resolve';
import { CardGrid, ChartCard, ScaledTrend } from './sections/common';
import {
  alignRows,
  gapThresholdMs,
  indexSeries,
  newestInstant,
  seriesStats,
  usable
} from './series';
import { groupByScale, largestGroup, sameScale, unitText } from './units';
import {
  resolveWindow,
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
 * bays - with bounded aggregate reads rather than a read per chart.
 */
export function StationPerformance({
  station
}: Readonly<{ station: PerformanceMachine }>) {
  const api = useApi();
  const theme = useMantineTheme();
  const scheme = useComputedColorScheme('light');
  const now = useClock();
  const [params, setParams] = useSearchParams();
  const dataRange = useDataRange(
    station.pk,
    params.get('history_clock') === 'recorded'
  );
  const linked = displayLinkedWindow(
    params,
    dataRange.isFetching || dataRange.isError
      ? undefined
      : dataRange.data?.display_shift_seconds
  );
  const invalidLink = linked.present && !linked.window;
  const range = useWindowState(
    linked.window ? { kind: 'absolute', ...linked.window } : undefined
  );
  const [investigationOpen, setInvestigationOpen] = useState(false);
  const signalsQuery = useMachineSignals(station.pk, range.live);

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
      [...(baysQuery.isError ? [] : (baysQuery.data?.bays ?? []))].sort(
        (a, b) => bayNumber(a.key) - bayNumber(b.key)
      ),
    [baysQuery.data, baysQuery.isError]
  );

  const keys = useMemo(
    () => [
      ...STATION_KEYS,
      ...bays.flatMap((bay) => Object.values(bayKeys(bay.key)))
    ],
    [bays]
  );
  // Not before the bay list has answered: a keys-only read of the station's
  // own points would be made and then repeated with every bay's keys - two
  // reads for one window. A failed bay list still reads the station's own.
  const targets = useMemo(
    () => (baysQuery.isPending || invalidLink ? {} : { keys }),
    [baysQuery.isPending, keys, invalidLink]
  );
  const seriesQuery = useMachineSeries(
    station.pk,
    targets,
    range.window,
    range.live
  );
  const response =
    seriesQuery.isError || invalidLink ? undefined : seriesQuery.data;
  const series = useMemo(() => indexSeries(response), [response]);
  const byKey = useMemo(() => {
    const map = new Map<string, SeriesEntry>();
    for (const entry of series.values()) map.set(entry.external_key, entry);
    return map;
  }, [series]);
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
      const available = bays
        .map((bay) => ({ bay, entry: byKey.get(pick(bayKeys(bay.key))) }))
        .filter(
          (item): item is { bay: (typeof bays)[number]; entry: SeriesEntry } =>
            !!item.entry?.available
        );
      // Only one unit can be summed (R6 A19.7): a bay reporting kW added to
      // bays in MW gave a total in neither. The largest same-unit group is
      // summed, nothing is rescaled, and the rest are named.
      const groups = groupByScale(available, (item) => item.entry.unit);
      const summed = largestGroup(groups)?.items ?? [];
      const entries = summed.map((item) => item.entry);
      const excluded = available
        .filter((item) => !summed.includes(item))
        .map(
          (item) =>
            `${item.bay.name.split('/').pop()?.trim() || item.bay.key} (${item.entry.unit || t`no unit`})`
        );
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
        // A paused or fixed window ends where it was frozen, so its newest
        // readings are that window's last ones, not the plant's present.
        caption: [
          range.live
            ? spread > gapThresholdMs(window.resolutionSeconds)
              ? t`Calculated: sum of ${lasts.length} of ${bays.length} bays' newest readings, spanning ${formatDuration(spread / 1000)}`
              : t`Calculated: sum of ${lasts.length} of ${bays.length} bays' newest readings`
            : spread > gapThresholdMs(window.resolutionSeconds)
              ? t`Calculated: sum of ${lasts.length} of ${bays.length} bays' last readings in the selected window, spanning ${formatDuration(spread / 1000)}`
              : t`Calculated: sum of ${lasts.length} of ${bays.length} bays' last readings in the selected window`,
          excluded.length > 0
            ? t`not summed, unit differs: ${excluded.join(', ')}`
            : ''
        ]
          .filter(Boolean)
          .join('; ')
      });
    };
    total((k) => k.power, t`Plant power`, 'plant-power');
    total((k) => k.flow, t`Plant flow`, 'plant-flow');
    return out;
  }, [parameters, series, bays, byKey, window.resolutionSeconds, range.live]);

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
    const rows = alignRows(
      present.map((item) => ({ key: item.key, entry: item.entry })),
      gapThresholdMs(window.resolutionSeconds)
    );
    const asLine = (item: (typeof present)[number]): TrendSeries => ({
      key: item.key,
      label: item.label,
      color: item.color,
      unit: item.entry?.unit ?? '',
      decimals: 2
    });
    // One axis, one unit (R6 A19.7): the comparison draws the largest
    // same-unit group of bays, and any bay in another unit gets a trend of
    // its own below rather than a line on the wrong scale.
    const groups = groupByScale(present, (item) => item.entry?.unit);
    const main = largestGroup(groups);
    const lines: TrendSeries[] = (main?.items ?? []).map(asLine);
    const unit = main ? unitText(main) : '';
    const apart = groups
      .filter((group) => group !== main)
      .map((group) => ({
        key: group.key,
        unit: unitText(group),
        lines: group.items.map(asLine)
      }));
    // A bay the source could not serve is named, not silently dropped: the
    // card used to lose its line, or vanish when no bay had history.
    const read = !!response && !seriesQuery.isError && !seriesQuery.isLoading;
    const failedTargets = failedHistoryTargets(response);
    const failed = read
      ? items
          .filter((item) => failedTargets.keys.has(pick(bayKeys(item.key))))
          .map((item) => item.label)
      : [];
    const unmapped = read
      ? items
          .filter((item) => !item.entry && !failed.includes(item.label))
          .map((item) => item.label)
      : [];
    const missing = read
      ? items
          .filter((item) => item.entry && !item.entry.available)
          .map((item) => item.label)
      : [];
    const details = read
      ? [
          ...new Set(
            items
              .filter((item) => item.entry && !item.entry.available)
              .map((item) => item.entry?.detail)
              .filter(Boolean)
          )
        ]
      : [];
    return { lines, rows, unit, missing, unmapped, details, apart, failed };
  };

  const power = useMemo(
    () => bayLines((k) => k.power, 'blue'),
    [
      bays,
      byKey,
      window.resolutionSeconds,
      theme,
      scheme,
      response,
      seriesQuery.isError,
      seriesQuery.isLoading
    ]
  );
  const flow = useMemo(
    () => bayLines((k) => k.flow, 'teal'),
    [
      bays,
      byKey,
      window.resolutionSeconds,
      theme,
      scheme,
      response,
      seriesQuery.isError,
      seriesQuery.isLoading
    ]
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
      bindingIds: [item.p.signal.binding_id],
      externalKeys: [item.p.signal.external_key],
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
    // A column's unit is the one most of its bays report, not merely the
    // first: a cell in any other unit carries its own (see `cell` below).
    const unitOf = (pick: (r: (typeof rows)[number]) => string) => {
      const group = largestGroup(groupByScale(rows.map(pick), (unit) => unit));
      return group ? unitText(group) : '';
    };
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

  const mimicGeneratedAt = baysQuery.data?.generated_at
    ? Date.parse(baysQuery.data.generated_at)
    : Number.NaN;
  const mimicTime = Number.isFinite(mimicGeneratedAt)
    ? mimicGeneratedAt
    : baysQuery.dataUpdatedAt;
  const mimicLabel =
    mimicTime > 0
      ? `${dayjs(mimicTime).format('D MMM YYYY HH:mm:ss')} ${clockLabel(mimicTime)}`
      : t`time not recorded`;
  const freshness = signalsQuery.data?.[0]?.freshness_threshold_seconds ?? null;
  // The station view had no word for a source that served nothing: its bay
  // cards emptied and the page looked merely quiet.
  const unavailable =
    response?.series.filter((entry) => !entry.available) ?? [];
  const failedTargets = failedHistoryTargets(response);
  const failedAll =
    !!response &&
    response.series.length > 0 &&
    unavailable.length === response.series.length;

  return (
    <HistoryReadStack response={response} gap='lg'>
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
              if (!invalidLink) seriesQuery.refetch();
            }}
            refreshing={seriesQuery.isFetching}
            dataRange={dataRange.data}
          />
          <WindowStatus
            response={response}
            updating={seriesQuery.isPlaceholderData}
            now={now}
          />
        </Stack>
        <LiveIndicator
          live={range.live}
          fetching={seriesQuery.isFetching}
          failed={seriesQuery.isError}
          newestAt={newestAt}
          updatedAt={seriesQuery.dataUpdatedAt || null}
          response={response}
          freshnessSeconds={freshness}
          now={now}
        />
      </Group>

      {invalidLink && (
        <Alert
          color='yellow'
          title={
            linked.clockUnknown
              ? t`Recorded clock unknown`
              : t`Invalid investigation window`
          }
        >
          {linked.clockUnknown
            ? t`The recorded-to-display clock offset is unavailable or still loading. No history read is made until the clock is known. Retry the source extent or choose a display-clock range.`
            : t`The linked window must have valid increasing timestamps and be no longer than 24 h. No history has been requested from this link.`}
          {linked.clockUnknown && (
            <Button
              size='xs'
              variant='subtle'
              onClick={() => dataRange.refetch()}
            >{t`Retry source extent`}</Button>
          )}
          <Button
            size='xs'
            variant='subtle'
            onClick={() =>
              setParams(
                (previous) => {
                  const next = new URLSearchParams(previous);
                  next.delete('history_from');
                  next.delete('history_to');
                  next.delete('history_clock');
                  return next;
                },
                { replace: true }
              )
            }
          >{t`Use current range controls`}</Button>
        </Alert>
      )}
      {baysQuery.isError && (
        <Alert color='red' title={t`Bay list unavailable`}>
          {t`The bay list could not be read; per-bay comparisons and the table are not shown. Station-only history remains available.`}
          <Group mt='xs'>
            <Button
              size='xs'
              variant='light'
              onClick={() => baysQuery.refetch()}
            >{t`Retry bay list`}</Button>
          </Group>
        </Alert>
      )}
      <LinkedEvidence machineId={station.pk} />
      <HistoryProvenance
        response={response}
        loading={seriesQuery.isFetching && !response}
        updating={seriesQuery.isPlaceholderData}
        failed={seriesQuery.isError}
        extent={dataRange.data}
        onRetry={() => seriesQuery.refetch()}
        currentDescription={t`Station source tiles use current observations. Calculated bay totals and bay tables use last usable readings in the selected window, with their own timestamps and coverage. These are separate origins; retained snapshots are separate evidence.`}
      />
      <Paper withBorder radius='md' p='sm'>
        <Button
          variant='subtle'
          size='xs'
          aria-expanded={investigationOpen}
          onClick={() => setInvestigationOpen((value) => !value)}
        >{t`Case span investigation`}</Button>
        {investigationOpen && (
          <InvestigationSlices
            initial={resolveWindow(range.window, now)}
            onRead={(span) => {
              range.onCustom(span.from, span.to);
            }}
          />
        )}
      </Paper>
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
      {failedAll && !seriesQuery.isError && (
        <Alert
          color='yellow'
          variant='light'
          icon={<IconAlertTriangle size={16} />}
          title={t`The source could not serve this window`}
        >
          {unavailable[0]?.detail ??
            t`No history is available for this window.`}
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
                <ScaledTrend
                  rows={stationLines.rows}
                  lines={stationLines.lines}
                  window={window}
                  unit={stationLines.unit}
                  rightUnit=''
                  syncId={syncId}
                  onZoom={range.onZoom}
                  toggleable={false}
                />
              </ChartCard>
            )}
            {bays.length > 0 && (
              <ChartCard
                title={t`Active power by bay`}
                description={
                  [
                    power.failed.length > 0
                      ? t`History read failed for ${power.failed.join(', ')}; coverage unknown.`
                      : '',
                    power.unmapped.length > 0
                      ? t`No power channel mapped for ${power.unmapped.join(', ')}.`
                      : '',
                    power.missing.length > 0
                      ? t`No history in this window for ${power.missing.join(', ')}.`
                      : '',
                    ...power.details
                  ]
                    .filter(Boolean)
                    .join(' ') || undefined
                }
              >
                <BayComparison
                  data={power}
                  window={window}
                  syncId={syncId}
                  onZoom={range.onZoom}
                />
              </ChartCard>
            )}
            {bays.length > 0 && (
              <ChartCard
                title={t`Discharge flow by bay`}
                description={
                  [
                    flow.failed.length > 0
                      ? t`History read failed for ${flow.failed.join(', ')}; coverage unknown.`
                      : '',
                    flow.unmapped.length > 0
                      ? t`No flow channel mapped for ${flow.unmapped.join(', ')}.`
                      : '',
                    flow.missing.length > 0
                      ? t`No history in this window for ${flow.missing.join(', ')}.`
                      : '',
                    ...flow.details
                  ]
                    .filter(Boolean)
                    .join(' ') || undefined
                }
              >
                <BayComparison
                  data={flow}
                  window={window}
                  syncId={syncId}
                  onZoom={range.onZoom}
                />
              </ChartCard>
            )}
          </CardGrid>

          {bays.length > 0 && (
            <Paper withBorder radius='md' p={0}>
              {/* Scrolls sideways rather than squeezing: at phone width the
                State column shrank its badges to "R…" and "I…". */}
              <Table.ScrollContainer minWidth={720} type='native'>
                <Table striped highlightOnHover>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>{t`Bay`}</Table.Th>
                      {/* A badge's clipped label has no minimum width of its own, so
                      the column keeps one or the table squeezes it. */}
                      <Table.Th miw={96}>{t`State`}</Table.Th>
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
                      const rowKeys = bayKeys(bay.key);
                      const failedCell = (
                        <Text size='xs'>{t`History read failed`}</Text>
                      );
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
                            {/* Without a status reading in the window the
                              state is the station's present word for the
                              bay - not something this window recorded. */}
                            {failedTargets.keys.has(rowKeys.status) &&
                              failedCell}
                            {(st?.v === null || st?.v === undefined) && (
                              <Text size='xs' c='dimmed'>
                                {Number.isFinite(mimicGeneratedAt)
                                  ? t`Station mimic read at ${mimicLabel}; not an observation time; not from this window`
                                  : t`Station mimic response time unavailable; browser read at ${mimicLabel}; not an observation time; not from this window`}
                              </Text>
                            )}
                          </Table.Td>
                          <Table.Td ta='right'>
                            {failedTargets.keys.has(rowKeys.power)
                              ? failedCell
                              : cell(
                                  p?.v,
                                  table.powerDp,
                                  row.units.power,
                                  table.powerUnit
                                )}
                          </Table.Td>
                          <Table.Td ta='right'>
                            {failedTargets.keys.has(rowKeys.flow)
                              ? failedCell
                              : cell(
                                  f?.v,
                                  table.flowDp,
                                  row.units.flow,
                                  table.flowUnit
                                )}
                          </Table.Td>
                          <Table.Td ta='right'>
                            {failedTargets.keys.has(rowKeys.speed)
                              ? failedCell
                              : cell(
                                  s?.v,
                                  table.speedDp,
                                  row.units.speed,
                                  table.speedUnit
                                )}
                          </Table.Td>
                          <Table.Td ta='right'>
                            {failedTargets.keys.has(rowKeys.power)
                              ? failedCell
                              : cell(
                                  peak?.max,
                                  table.powerDp,
                                  row.units.power,
                                  table.powerUnit
                                )}
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
              </Table.ScrollContainer>
              {response && (
                <Text size='xs' c='dimmed' px='sm' py={6}>
                  {range.live
                    ? t`Each bay's newest usable reading in the window on screen (${formatWindow(window.start, window.end, now)}); window peak over the same span.`
                    : t`Each bay's last usable reading in the selected window (${formatWindow(window.start, window.end, now)}), not its present value; window peak over the same span.`}
                </Text>
              )}
            </Paper>
          )}
        </Stack>
      </div>
    </HistoryReadStack>
  );
}

/**
 * A table cell's number. The column header carries the unit its bays share;
 * a bay reporting in another unit prints its own beside the number rather
 * than appearing to read in the header's.
 */
function cell(
  value: number | null | undefined,
  decimals: number,
  unit: string,
  columnUnit: string
): string {
  if (value === null || value === undefined) return '—';
  const text = formatAt(value, decimals);
  return sameScale(unit, columnUnit)
    ? text
    : unit
      ? `${text} ${unit}`
      : `${text} (${t`no unit`})`;
}

/** One per-bay comparison: the main same-unit trend and any set apart. */
function BayComparison({
  data,
  window,
  syncId,
  onZoom
}: Readonly<{
  data: {
    lines: TrendSeries[];
    rows: ReturnType<typeof alignRows>;
    unit: string;
    apart: { key: string; unit: string; lines: TrendSeries[] }[];
    failed: string[];
  };
  window: ReturnType<typeof windowInfoFrom>;
  syncId: string;
  onZoom: (from: number, to: number) => void;
}>) {
  const chart = (lines: TrendSeries[], unit: string) => (
    <TrendChart
      emptyText={
        data.failed.length > 0
          ? t`History read failed; coverage unknown.`
          : undefined
      }
      rows={data.rows}
      series={lines}
      windowStart={window.start}
      windowEnd={window.end}
      windowSeconds={window.seconds}
      unit={unit}
      syncId={syncId}
      toggleable
      onZoom={onZoom}
    />
  );
  if (data.apart.length === 0) return chart(data.lines, data.unit);
  return (
    <Stack gap='sm' data-units-split={data.apart.length}>
      {chart(data.lines, data.unit)}
      {data.apart.map((group) => (
        <Stack gap={4} key={group.key}>
          <Text size='xs' c='dimmed' data-units-apart={group.unit}>
            {t`Drawn apart: ${group.lines.map((l) => l.label).join(', ')} in ${group.unit || t`no unit`}, not ${data.unit || t`no unit`}.`}
          </Text>
          {chart(group.lines, group.unit)}
        </Stack>
      ))}
    </Stack>
  );
}

export default StationPerformance;
