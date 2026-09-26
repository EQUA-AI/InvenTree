import { t } from '@lingui/core/macro';
import { BarChart } from '@mantine/charts';
import { Badge, Group, Paper, SimpleGrid, Stack, Text } from '@mantine/core';
import { useMemo } from 'react';

import type { SeriesEntry } from '@lib/types/MachineHealth';

import { SensorHeatmap } from './SensorHeatmap';
import {
  type ReferenceLine,
  TrendChart,
  type TrendSeries,
  familyColors
} from './TrendChart';
import { formatValue } from './format';
import type { ResolvedParameter } from './resolve';
import {
  alignRows,
  gapThresholdMs,
  heatmapMatrix,
  limitState,
  seriesStats
} from './series';

export interface WindowInfo {
  start: number;
  end: number;
  seconds: number;
  resolutionSeconds: number;
}

export interface SensorGroupCardProps {
  title: string;
  /** One family: sensors that share a unit and a meaning. */
  parameters: ResolvedParameter[];
  series: Map<number, SeriesEntry>;
  window: WindowInfo;
  syncId: string;
  hue?: string;
  /** Sensors × time, when there are enough sensors for a matrix to say more than lines. */
  heatmap?: boolean;
  /** Current value per sensor, side by side. */
  comparison?: boolean;
  onZoom?: (from: number, to: number) => void;
  description?: string;
}

/**
 * A family of like sensors - twelve winding RTDs, ten thrust pads, four
 * cooling water inlets - in one card.
 *
 * The header answers the questions an operator asks first: which sensor is
 * highest, how far apart the family is, and whether any of them is outside a
 * configured limit. The chart draws them on one axis; the heatmap, when there
 * are enough of them, shows the whole family's history at once; the comparison
 * ranks their current readings. Everything derived is labelled calculated.
 */
export function SensorGroupCard({
  title,
  parameters,
  series,
  window,
  syncId,
  hue = 'blue',
  heatmap = false,
  comparison = false,
  onZoom,
  description
}: Readonly<SensorGroupCardProps>) {
  const unit = parameters[0]?.signal.unit ?? '';
  const decimals = parameters[0]?.definition.decimals ?? 1;
  const colors = useMemo(
    () => familyColors(parameters.length, [hue]),
    [parameters.length, hue]
  );

  const lines: TrendSeries[] = useMemo(
    () =>
      parameters.map((p, i) => ({
        key: p.id,
        label: p.label,
        color: colors[i],
        unit: p.signal.unit,
        decimals: p.definition.decimals
      })),
    [parameters, colors]
  );

  const rows = useMemo(
    () =>
      alignRows(
        parameters.map((p) => ({
          key: p.id,
          entry: series.get(p.signal.binding_id)
        })),
        gapThresholdMs(window.resolutionSeconds)
      ),
    [parameters, series, window.resolutionSeconds]
  );

  // Current readings come from the signals (the machine's present state), the
  // window statistics from the series; the two are kept apart in the labels.
  const current = useMemo(
    () =>
      parameters.map((p) => ({
        parameter: p,
        value:
          typeof p.signal.value === 'number' && Number.isFinite(p.signal.value)
            ? p.signal.value
            : null,
        stale: p.signal.stale,
        state: limitState(
          typeof p.signal.value === 'number' ? p.signal.value : null,
          p.signal.limits
        )
      })),
    [parameters]
  );

  const summary = useMemo(() => {
    const live = current.filter((c) => c.value !== null && !c.stale);
    if (live.length === 0) return null;
    const values = live.map((c) => c.value as number);
    const highest = live.reduce((a, b) =>
      (b.value as number) > (a.value as number) ? b : a
    );
    const lowest = live.reduce((a, b) =>
      (b.value as number) < (a.value as number) ? b : a
    );
    const over = current.filter(
      (c) => c.state === 'warning' || c.state === 'critical'
    );
    const configured = current.filter((c) => c.state !== 'unconfigured');
    return {
      highest,
      lowest,
      mean: values.reduce((a, b) => a + b, 0) / values.length,
      spread: (highest.value as number) - (lowest.value as number),
      over: over.length,
      configured: configured.length,
      live: live.length
    };
  }, [current]);

  // Reference lines only when the whole family shares one configured limit;
  // a line labelled "warn 125" over sensors with different limits would lie.
  const referenceLines: ReferenceLine[] = useMemo(() => {
    const limits = parameters.map((p) => p.signal.limits);
    const shared = (
      name: 'warn_max' | 'critical_max' | 'warn_min' | 'critical_min'
    ) => {
      const values = new Set(limits.map((l) => l[name]));
      return values.size === 1 ? limits[0][name] : null;
    };
    const lines: ReferenceLine[] = [];
    const warnMax = shared('warn_max');
    const criticalMax = shared('critical_max');
    const warnMin = shared('warn_min');
    const criticalMin = shared('critical_min');
    if (warnMax !== null)
      lines.push({
        y: warnMax,
        label: t`Warn ${formatValue(warnMax, decimals, unit)}`,
        color: 'yellow.7'
      });
    if (criticalMax !== null)
      lines.push({
        y: criticalMax,
        label: t`Critical ${formatValue(criticalMax, decimals, unit)}`,
        color: 'red.7'
      });
    if (warnMin !== null)
      lines.push({
        y: warnMin,
        label: t`Warn ${formatValue(warnMin, decimals, unit)}`,
        color: 'yellow.7'
      });
    if (criticalMin !== null)
      lines.push({
        y: criticalMin,
        label: t`Critical ${formatValue(criticalMin, decimals, unit)}`,
        color: 'red.7'
      });
    return lines;
  }, [parameters, decimals, unit]);

  const matrix = useMemo(
    () =>
      heatmap && parameters.length >= 4
        ? heatmapMatrix(
            parameters.map((p) => ({
              key: p.id,
              label: p.label,
              entry: series.get(p.signal.binding_id)
            })),
            window.start,
            window.end,
            Math.min(
              48,
              Math.max(
                12,
                Math.floor(
                  window.seconds / Math.max(window.resolutionSeconds, 1)
                )
              )
            )
          )
        : null,
    [heatmap, parameters, series, window]
  );

  const bars = useMemo(
    () =>
      comparison
        ? current
            .map((c) => ({
              sensor: c.parameter.label,
              value: c.value,
              color:
                c.state === 'critical'
                  ? 'red.7'
                  : c.state === 'warning'
                    ? 'yellow.6'
                    : c.stale
                      ? 'gray.4'
                      : `${hue}.6`
            }))
            .sort(
              (a, b) =>
                (b.value ?? Number.NEGATIVE_INFINITY) -
                (a.value ?? Number.NEGATIVE_INFINITY)
            )
        : [],
    [comparison, current, hue]
  );

  // The chart colours a bar by its value alone, so the state colour is looked
  // up by value; two sensors reading exactly alike share a colour, which is
  // the least surprising outcome.
  const barColors = useMemo(() => {
    const map = new Map<number, string>();
    for (const bar of bars) {
      if (bar.value !== null && !map.has(bar.value))
        map.set(bar.value, bar.color);
    }
    return map;
  }, [bars]);

  const windowStats = useMemo(() => {
    let max: { label: string; value: number } | null = null;
    for (const p of parameters) {
      const stats = seriesStats(series.get(p.signal.binding_id));
      if (stats && (max === null || stats.max > max.value)) {
        max = { label: p.label, value: stats.max };
      }
    }
    return { max };
  }, [parameters, series]);

  if (parameters.length === 0) {
    return null;
  }

  return (
    <Paper withBorder radius='md' p='md'>
      <Stack gap='sm'>
        <Group justify='space-between' align='flex-start' wrap='wrap' gap='xs'>
          <Stack gap={2}>
            <Text fw={600}>{title}</Text>
            {description && (
              <Text size='xs' c='dimmed'>
                {description}
              </Text>
            )}
          </Stack>
          {summary && (
            <Group gap='xs' wrap='wrap'>
              <Badge variant='light' color={hue} size='sm'>
                {t`Highest now: ${summary.highest.parameter.label} ${formatValue(summary.highest.value, decimals, unit, summary.spread)}`}
              </Badge>
              <Badge variant='light' color='gray' size='sm'>
                {t`Mean ${formatValue(summary.mean, decimals, unit, summary.spread)}`}
              </Badge>
              {summary.live > 1 && (
                <Badge variant='light' color='gray' size='sm'>
                  {t`Spread ${formatValue(summary.spread, decimals, unit, summary.spread)} (calculated)`}
                </Badge>
              )}
              {windowStats.max && (
                <Badge variant='light' color='gray' size='sm'>
                  {t`Window peak ${formatValue(windowStats.max.value, decimals, unit)} (${windowStats.max.label})`}
                </Badge>
              )}
              {summary.configured > 0 ? (
                <Badge
                  variant='light'
                  color={summary.over > 0 ? 'red' : 'green'}
                  size='sm'
                >
                  {summary.over > 0
                    ? t`${summary.over} of ${summary.configured} over a limit`
                    : t`${summary.configured} within limits`}
                </Badge>
              ) : (
                <Badge variant='light' color='gray' size='sm'>
                  {t`No limits configured`}
                </Badge>
              )}
            </Group>
          )}
        </Group>

        <TrendChart
          rows={rows}
          series={lines}
          windowStart={window.start}
          windowEnd={window.end}
          windowSeconds={window.seconds}
          unit={unit}
          referenceLines={referenceLines}
          syncId={syncId}
          toggleable={parameters.length > 1}
          onZoom={onZoom}
          height={parameters.length > 6 ? 280 : 220}
        />

        {(matrix || bars.length > 1) && (
          <SimpleGrid
            cols={{ base: 1, lg: matrix && bars.length > 1 ? 2 : 1 }}
            spacing='md'
          >
            {matrix && (
              <Stack gap={4}>
                <Text size='sm' fw={500}>
                  {t`Sensors over time`}
                </Text>
                <SensorHeatmap
                  matrix={matrix}
                  unit={unit}
                  decimals={decimals}
                  windowSeconds={window.seconds}
                  hue={hue}
                />
              </Stack>
            )}
            {bars.length > 1 && (
              <Stack gap={4}>
                <Text size='sm' fw={500}>
                  {t`Current reading by sensor`}
                </Text>
                <BarChart
                  h={Math.max(120, bars.length * 22 + 30)}
                  data={bars.map((b) => ({
                    sensor: b.sensor,
                    value: b.value ?? 0,
                    color: b.color
                  }))}
                  dataKey='sensor'
                  orientation='vertical'
                  series={[
                    {
                      name: 'value',
                      label: unit || t`Value`,
                      color: `${hue}.6`
                    }
                  ]}
                  getBarColor={(value) => barColors.get(value) ?? `${hue}.6`}
                  withLegend={false}
                  valueFormatter={(value: number) =>
                    formatValue(value, decimals, unit, summary?.spread)
                  }
                  yAxisProps={{
                    width: 150,
                    interval: 0,
                    tick: { fontSize: 11 }
                  }}
                  xAxisProps={{ domain: ['auto', 'auto'] }}
                  barProps={{ isAnimationActive: false, radius: 2 }}
                  tooltipAnimationDuration={0}
                />
                {bars.some((b) => b.value === null) && (
                  <Text size='xs' c='dimmed'>
                    {t`A sensor with no current reading is drawn at zero and greyed; zero is not its value.`}
                  </Text>
                )}
              </Stack>
            )}
          </SimpleGrid>
        )}
      </Stack>
    </Paper>
  );
}

export default SensorGroupCard;
