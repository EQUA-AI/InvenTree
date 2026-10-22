import { t } from '@lingui/core/macro';
import { ScatterChart } from '@mantine/charts';
import {
  Box,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  useComputedColorScheme,
  useMantineTheme
} from '@mantine/core';
import { useMemo } from 'react';
import { ValueTooltip } from '../../../components/items/ValueTooltip';
import { StatusBadge } from '../health/StatusBadge';
import { MetaText } from '../health/common';
import { hasLimits } from './evaluation';
import { limitLines } from './limitLines';

import type { SeriesEntry } from '@lib/types/MachineHealth';

import { SensorHeatmap, measureText } from './SensorHeatmap';
import {
  type ReferenceLine,
  TrendChart,
  type TrendSeries,
  familyColors
} from './TrendChart';
import { formatValue, niceTicks, unitLabel } from './format';
import type { ResolvedParameter } from './resolve';
import {
  alignRows,
  gapThresholdMs,
  heatmapMatrix,
  resolutionForSeries,
  seriesStats
} from './series';
import { groupByScale, unitText } from './units';

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
 * A family of like sensors, one card per scale (R6 A19.7).
 *
 * The family card assumes one unit: its axis, heatmap scale, bars and
 * "highest now" all compare members with each other. A family whose
 * dictionary units differ - a draft-tube pressure beside a draft-tube level,
 * an RTD in degF among degC - is drawn as one card per unit, retaining forms supported by the unit group size, without ranking one reading on another's scale.
 */
export function SensorGroupCard(props: Readonly<SensorGroupCardProps>) {
  const groups = useMemo(
    () => groupByScale(props.parameters, (p) => p.signal.unit),
    [props.parameters]
  );
  if (groups.length <= 1) return <SensorGroupCardBody {...props} />;
  const units = groups.map((g) => unitLabel(unitText(g)));
  return (
    <Stack gap='md' data-units-split={groups.length}>
      {groups.map((group, index) => (
        <SensorGroupCardBody
          key={group.key}
          {...props}
          parameters={group.items}
          title={t`${props.title} (${units[index]})`}
          description={[
            props.description,
            t`This family reports in ${units.join(', ')}; each unit has its own card, so no reading is compared on another's scale.`,
            props.heatmap && group.items.length < 4
              ? t`Heatmap not drawn: this unit group has fewer than four sensors.`
              : ''
          ]
            .filter(Boolean)
            .join(' ')}
        />
      ))}
    </Stack>
  );
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
function SensorGroupCardBody({
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
  const theme = useMantineTheme();
  const scheme = useComputedColorScheme('light');
  const resolutionSeconds = resolutionForSeries(
    parameters.map((p) => series.get(p.signal.binding_id)),
    window.resolutionSeconds
  );
  const unit = parameters[0]?.signal.unit ?? '';
  const decimals = parameters[0]?.definition.decimals ?? 1;
  const colors = useMemo(
    () => familyColors(parameters.length, [hue], theme, scheme),
    [parameters.length, hue, theme, scheme]
  );

  const lines: TrendSeries[] = useMemo(
    () =>
      parameters.map((p, i) => ({
        key: p.id,
        bindingIds: [p.signal.binding_id],
        externalKeys: [p.signal.external_key],
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
        gapThresholdMs(resolutionSeconds)
      ),
    [parameters, series, resolutionSeconds]
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
        good: p.signal.quality === 'good',
        state:
          !p.signal.stale &&
          p.signal.quality === 'good' &&
          Object.values(p.signal.limits).some((value) => value != null)
            ? p.signal.state
            : 'unconfigured'
      })),
    [parameters]
  );

  const summary = useMemo(() => {
    // Good-quality, current readings only; a pegged sensor is neither the
    // highest nor part of the mean.
    const live = current.filter((c) => c.value !== null && !c.stale && c.good);
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
    const configured = current.filter((c) =>
      ['normal', 'warning', 'critical'].includes(c.state)
    );
    return {
      highest,
      lowest,
      mean: values.reduce((a, b) => a + b, 0) / values.length,
      spread: (highest.value as number) - (lowest.value as number),
      over: over.length,
      worst: over.some((entry) => entry.state === 'critical')
        ? ('critical' as const)
        : ('warning' as const),
      breaches: over,
      configured: configured.length,
      mappedLimits: parameters.filter((p) => hasLimits(p.signal)).length,
      live: live.length
    };
  }, [current, parameters]);

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
    return limitLines(
      {
        warn_max: shared('warn_max'),
        warn_min: shared('warn_min'),
        critical_max: shared('critical_max'),
        critical_min: shared('critical_min')
      },
      unit,
      decimals
    );
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
                Math.floor(window.seconds / Math.max(resolutionSeconds, 1))
              )
            )
          )
        : null,
    [heatmap, parameters, series, window, resolutionSeconds]
  );

  const bars = useMemo(
    () =>
      comparison
        ? current
            .map((c) => ({
              sensor: c.parameter.label,
              value: c.good && !c.stale ? c.value : null,
              color: colors[0]
            }))
            .sort(
              (a, b) =>
                (b.value ?? Number.NEGATIVE_INFINITY) -
                (a.value ?? Number.NEGATIVE_INFINITY)
            )
        : [],
    [comparison, current, hue]
  );

  // Only a sensor with a usable reading has a bar. Feeding a null as zero drew
  // a bar of no length at a baseline the reader could not see, and the caption
  // pointed at empty space; the sensors are named instead, below the chart.
  const readable = useMemo(() => bars.filter((b) => b.value !== null), [bars]);
  const unreadable = useMemo(
    () => bars.filter((b) => b.value === null),
    [bars]
  );

  // Recharts reserves what it is given for a vertical chart's category axis
  // and wraps a longer name inside it, so a fixed width wrapped every sensor
  // name onto two lines that then overlapped the row below. Measure the
  // widest name and reserve that, and give a row that still wraps the height
  // two lines need.
  const barAxis = useMemo(() => {
    const widest = Math.max(0, ...readable.map((b) => measureText(b.sensor)));
    // Recharts wraps a name the moment its words plus a space each reach the
    // width it was given, so the room it is told about is a few pixels more
    // than the name measures; the axis then holds that plus its tick gutter.
    const text = Math.min(226, Math.ceil(widest) + 10);
    return {
      text,
      width: Math.max(110, text + 14),
      wraps: Math.ceil(widest) + 10 > text
    };
  }, [readable]);

  const dotValues = [
    ...readable.map((point) => point.value as number),
    ...referenceLines.map((line) => line.y)
  ];
  const dotMin = dotValues.length ? Math.min(...dotValues) : 0;
  const dotMax = dotValues.length ? Math.max(...dotValues) : 1;
  const dotTicks = niceTicks(
    dotMin === dotMax ? dotMin - 1 : dotMin,
    dotMin === dotMax ? dotMax + 1 : dotMax
  );
  const dotDomain: [number, number] = [dotTicks[0], dotTicks.at(-1)!];
  const comparisonLimits = referenceLines.filter(
    (line) => line.y >= dotDomain[0] && line.y <= dotDomain[1]
  );
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

  const breachedBounds =
    summary?.breaches.flatMap((entry) =>
      limitLines(entry.parameter.signal.limits, unit, decimals).filter(
        (line) =>
          line.severity === summary.worst &&
          (line.direction === 'high'
            ? (entry.value as number) > line.y
            : (entry.value as number) < line.y)
      )
    ) ?? [];
  const directions = new Set(breachedBounds.map((line) => line.direction));
  const breachWord =
    directions.size === 1
      ? directions.has('high')
        ? t`above`
        : t`below`
      : t`outside`;
  if (parameters.length === 0) {
    return null;
  }

  return (
    <Paper withBorder p='md'>
      <Stack gap='sm'>
        <Group justify='space-between' align='flex-start' wrap='wrap' gap='xs'>
          <Stack gap={2}>
            <Text fw={600}>{title}</Text>
            {!unit && (
              <ValueTooltip label={description || t`Unit not confirmed`}>
                <StatusBadge
                  tone='neutral'
                  size='sm'
                  color='gray'
                >{t`Unit not confirmed`}</StatusBadge>
              </ValueTooltip>
            )}
          </Stack>
          {summary && (
            <Group gap='xs' wrap='wrap'>
              <StatusBadge
                tone='neutral'
                variant='outline'
                color='gray'
                size='sm'
              >
                {t`Highest now: ${summary.highest.parameter.label} ${formatValue(summary.highest.value, decimals, unit, summary.spread)}`}
              </StatusBadge>

              {summary.configured > 0 ? (
                <StatusBadge
                  variant={summary.over > 0 ? 'filled' : 'outline'}
                  color={
                    summary.over > 0
                      ? summary.worst === 'critical'
                        ? 'red.9'
                        : 'yellow.6'
                      : 'green'
                  }
                  tone={summary.over > 0 ? summary.worst : 'normal'}
                  size='sm'
                >
                  {summary.over > 0
                    ? t`${summary.over} of ${summary.configured} ${breachWord} ${summary.worst} (${breachedBounds.map((line) => line.label).join(' · ') || t`breach boundary not supplied`})`
                    : t`${summary.configured} within limits`}
                </StatusBadge>
              ) : (
                <StatusBadge
                  tone='neutral'
                  variant='outline'
                  color='gray'
                  size='sm'
                >
                  {parameters.some((p) => hasLimits(p.signal))
                    ? t`Limits not assessed`
                    : t`No limits configured`}
                </StatusBadge>
              )}
              {summary.mappedLimits > summary.configured && (
                <MetaText size='xs'>{t`${summary.configured} of ${summary.mappedLimits} configured sensors evaluated now`}</MetaText>
              )}
            </Group>
          )}
        </Group>

        <TrendChart
          rows={rows}
          series={lines.map((line) => ({
            ...line,
            strokeWidth: summary?.highest.parameter.id === line.key ? 2.5 : 1.5,
            endLabel: summary?.highest.parameter.id === line.key
          }))}
          windowStart={window.start}
          windowEnd={window.end}
          windowSeconds={window.seconds}
          unit={unit}
          referenceLines={referenceLines}
          syncId={syncId}
          title={title}
          toggleable={parameters.length > 1}
          onZoom={onZoom}
          height={parameters.length > 6 ? 280 : 220}
        />

        {(matrix || readable.length > 1) && (
          <SimpleGrid
            cols={{ base: 1, lg: matrix && readable.length > 1 ? 2 : 1 }}
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
            {readable.length > 1 && (
              <Stack gap={4} data-testid='sensor-comparison'>
                <Text size='sm' fw={500}>
                  {t`Current reading by sensor`}
                </Text>
                {comparisonLimits.length > 0 && (
                  <Group gap='sm' data-testid='sensor-comparison-limits'>
                    {comparisonLimits.map((line) => (
                      <Group key={line.label} gap={5} wrap='nowrap'>
                        <Box
                          component='span'
                          aria-hidden='true'
                          c={
                            scheme === 'dark'
                              ? `${line.severity === 'critical' ? 'red' : 'yellow'}.4`
                              : line.color
                          }
                          w={16}
                          style={{ borderTop: '2px dashed currentColor' }}
                        />
                        <Text size='xs'>{line.label}</Text>
                      </Group>
                    ))}
                  </Group>
                )}
                <ScatterChart
                  textColor='var(--mantine-color-text)'
                  h={Math.max(160, readable.length * 34 + 64)}
                  data={[
                    {
                      name: t`Current readings`,
                      color: colors[0],
                      data: readable.map((point, index) => ({
                        value: point.value as number,
                        position: index
                      }))
                    }
                  ]}
                  dataKey={{ x: 'value', y: 'position' }}
                  xAxisLabel={unitLabel(unit)}
                  styles={{ axisLabel: { fill: 'var(--mantine-color-text)' } }}
                  withLegend={false}
                  scatterChartProps={{
                    margin: { top: 12, right: 24, bottom: 24, left: 8 }
                  }}
                  xAxisProps={{
                    domain: dotDomain,
                    ticks: dotTicks,
                    tickFormatter: (value: number) =>
                      formatValue(value, decimals, undefined, summary?.spread)
                  }}
                  yAxisProps={{
                    domain: [-0.5, readable.length - 0.5],
                    ticks: readable.map((_, index) => index),
                    width: barAxis.width,
                    tickFormatter: (index: number) =>
                      readable[index]?.sensor ?? '',
                    interval: 0,
                    tick: ({ x, y, payload }) => (
                      <text
                        x={Number(x) - 8}
                        y={y}
                        textAnchor='end'
                        dominantBaseline='middle'
                        fill='var(--mantine-color-text)'
                        fontSize={11}
                      >
                        <title>{readable[Number(payload.value)]?.sensor}</title>
                        {(readable[Number(payload.value)]?.sensor ?? '')
                          .length > 28
                          ? `${readable[Number(payload.value)].sensor.slice(0, 27)}…`
                          : readable[Number(payload.value)]?.sensor}
                      </text>
                    )
                  }}
                  valueFormatter={{
                    x: (value: number) =>
                      formatValue(value, decimals, unit, summary?.spread),
                    y: (index: number) => readable[index]?.sensor ?? ''
                  }}
                  referenceLines={comparisonLimits.map((line) => ({
                    x: line.y,
                    color:
                      scheme === 'dark'
                        ? `${line.severity === 'critical' ? 'red' : 'yellow'}.4`
                        : line.color
                  }))}
                  scatterProps={{ isAnimationActive: false }}
                  tooltipAnimationDuration={0}
                />
                {unreadable.length > 0 && (
                  <MetaText size='xs'>
                    {t`No usable current reading, so not plotted: ${unreadable.map((b) => b.sensor).join(', ')}`}
                  </MetaText>
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
