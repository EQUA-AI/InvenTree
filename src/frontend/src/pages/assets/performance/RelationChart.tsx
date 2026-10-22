import { t } from '@lingui/core/macro';
import { ScatterChart } from '@mantine/charts';
import {
  Group,
  Paper,
  Stack,
  Text,
  useComputedColorScheme
} from '@mantine/core';
import { useMemo } from 'react';
import { MetaText } from '../health/common';

import {
  formatInstant,
  formatTick,
  formatValue,
  niceTicks,
  unitLabel
} from './format';
import type { RelationPoint } from './series';

/** One plotted point: the pair, its instant, and the band it was drawn in. */
interface BandPoint {
  x: number;
  y: number;
  t: number;
  name?: string;
}

export interface RelationChartProps {
  title: string;
  points: RelationPoint[];
  xLabel: string;
  yLabel: string;
  xUnit: string;
  yUnit: string;
  xDecimals: number;
  yDecimals: number;
  windowSeconds: number;
  height?: number;
}

/**
 * How two parameters moved together over the window.
 *
 * Each point is one snapshot in which both were read. Colour strengthens
 * toward the present, in three bands, so a drift over the window shows as a
 * drift across the plot. The titles say "relationship" and the caption says
 * what a point is; nothing here claims one parameter drives the other.
 */
export function RelationChart({
  title,
  points,
  xLabel,
  yLabel,
  xUnit,
  yUnit,
  xDecimals,
  yDecimals,
  windowSeconds,
  height = 240
}: Readonly<RelationChartProps>) {
  const scheme = useComputedColorScheme('light');
  // Both axes may hold a parameter that never moved, and a near-constant one
  // still needs enough decimals to tell its ticks apart - the valve position
  // that sits at 100.2966-100.3002 printed the same "100.3 percent" five
  // times while the plot plainly showed two clusters.
  const extent = useMemo(() => {
    const spread = (pick: (p: RelationPoint) => number) => {
      const values = points.map(pick).filter((v) => Number.isFinite(v));
      if (values.length === 0)
        return { min: Number.NaN, max: Number.NaN, range: null };
      const min = Math.min(...values);
      const max = Math.max(...values);
      return { min, max, range: max - min };
    };
    return { x: spread((p) => p.x), y: spread((p) => p.y) };
  }, [points]);

  const yTicks = niceTicks(
    extent.y.min === extent.y.max ? extent.y.min - 1 : extent.y.min,
    extent.y.min === extent.y.max ? extent.y.max + 1 : extent.y.max
  );
  const yDomain: [number, number] = [yTicks[0], yTicks.at(-1)!];
  const xTicks = niceTicks(
    extent.x.min === extent.x.max ? extent.x.min - 1 : extent.x.min,
    extent.x.min === extent.x.max ? extent.x.max + 1 : extent.x.max
  );
  const xDomain: [number, number] = [xTicks[0], xTicks.at(-1)!];

  // As wide as the longest tick it will print, for the same reason the trend
  // charts measure theirs: a fixed 56px wraps every pressure tick onto two
  // lines. The ticks are what is measured, not the readings - Recharts rounds
  // the extremes outwards and appends the unit, so "0" became "-0.5 MW".
  const yAxisWidth = useMemo(() => {
    const suffix = yUnit ? ` ${yUnit}`.length : 0;
    const [low, high] = yDomain;
    const drawn =
      typeof low === 'number' && typeof high === 'number'
        ? niceTicks(low, high)
        : niceTicks(extent.y.min, extent.y.max);
    const values = drawn.length > 0 ? drawn : [extent.y.min, extent.y.max];
    const longest = Math.max(
      ...values
        .filter((v) => Number.isFinite(v))
        .map(
          (v) =>
            formatValue(v, yDecimals, undefined, extent.y.range).length + suffix
        ),
      1
    );
    // Seven pixels a character at the 12px ticks Recharts draws, plus the
    // rotated axis title standing inside the same width and the tick margin.
    return 22 + 7 * Math.min(longest, 14);
  }, [extent.y, yDecimals, yUnit, yDomain]);

  const bands = useMemo(() => {
    if (points.length === 0) return [];
    const third = Math.max(1, Math.ceil(points.length / 3));
    const slices = [
      points.slice(0, third),
      points.slice(third, third * 2),
      points.slice(third * 2)
    ].filter((slice) => slice.length > 0);
    // Further from the card background means nearer the present, which means
    // opposite ladders per scheme: on the dark card the oldest band is the
    // dimmest, on white it is the palest. One fixed ladder made dark mode run
    // bright-to-dim, so the newest points were the hardest to see.
    const colors =
      scheme === 'dark'
        ? ['indigo.8', 'blue.6', 'cyan.3']
        : ['cyan.8', 'blue.7', 'indigo.9'];
    return slices.map((slice, i) => ({
      color: colors[i + (3 - slices.length)] ?? 'blue.6',
      name: t`${formatTick(slice[0].t, windowSeconds)} – ${formatTick(slice[slice.length - 1].t, windowSeconds)}`,
      // The instant travels with the point: the tooltip names the snapshot,
      // not the twenty-minute band it fell in.
      data: slice.map((p) => ({ x: p.x, y: p.y, t: p.t }))
    }));
  }, [points, windowSeconds, scheme]);

  return (
    <Paper withBorder p='sm'>
      <Stack gap={4}>
        <Text size='sm' fw={600}>
          {title}
        </Text>
        {points.length < 3 ? (
          // As tall as the chart it stands in for, so a card in a row of them
          // does not collapse, and specific about how few were paired.
          <MetaText size='sm' py='lg' mih={height}>
            {t`Only ${points.length} paired readings in this window; too few to show a relationship.`}
          </MetaText>
        ) : (
          <>
            <ScatterChart
              textColor='var(--mantine-color-text)'
              styles={{
                axisLabel: {
                  fill: 'var(--mantine-color-text)'
                }
              }}
              h={height}
              data={bands}
              dataKey={{ x: 'x', y: 'y' }}
              xAxisLabel={xUnit ? `${xLabel} (${unitLabel(xUnit)})` : xLabel}
              yAxisLabel={yUnit ? `${yLabel} (${unitLabel(yUnit)})` : yLabel}
              labels={{ x: xLabel, y: yLabel }}
              unit={{
                x: '',
                y: ''
              }}
              valueFormatter={{
                x: (value: number) =>
                  formatValue(value, xDecimals, undefined, extent.x.range),
                y: (value: number) =>
                  formatValue(value, yDecimals, undefined, extent.y.range)
              }}
              xAxisProps={{ domain: xDomain, ticks: xTicks }}
              yAxisProps={{ domain: yDomain, ticks: yTicks, width: yAxisWidth }}
              withLegend
              // The reserved band has to cover what the legend renders, or its
              // labels are cut off by the plot below them.
              legendProps={{ verticalAlign: 'bottom', height: 40 }}
              scatterProps={{ isAnimationActive: false }}
              tooltipAnimationDuration={0}
              tooltipProps={{
                content: ({ payload }) => (
                  <PointTooltip
                    datum={
                      (payload?.[0] as { payload?: BandPoint } | undefined)
                        ?.payload ?? null
                    }
                    xLabel={xLabel}
                    yLabel={yLabel}
                    xUnit={xUnit}
                    yUnit={yUnit}
                    xDecimals={xDecimals}
                    yDecimals={yDecimals}
                    xRange={extent.x.range}
                    yRange={extent.y.range}
                  />
                )
              }}
            />
          </>
        )}
      </Stack>
    </Paper>
  );
}

/**
 * One snapshot, headed by its instant.
 *
 * The default scatter tooltip is headed by the hovered series' name, which
 * here is a twenty-minute band: hovering one point then read as if the point
 * itself covered twenty minutes. The band keeps a dimmed line of its own, so
 * the colour legend is still readable from the tooltip.
 */
function PointTooltip({
  datum,
  xLabel,
  yLabel,
  xUnit,
  yUnit,
  xDecimals,
  yDecimals,
  xRange,
  yRange
}: Readonly<{
  datum: BandPoint | null;
  xLabel: string;
  yLabel: string;
  xUnit: string;
  yUnit: string;
  xDecimals: number;
  yDecimals: number;
  xRange: number | null;
  yRange: number | null;
}>) {
  if (!datum) {
    return null;
  }
  const row = (label: string, text: string) => (
    <Group justify='space-between' gap='md' wrap='nowrap'>
      <Text size='xs'>{label}</Text>
      <Text size='xs' fw={600}>
        {text}
      </Text>
    </Group>
  );
  return (
    <Paper withBorder shadow='sm' p='xs' style={{ minWidth: 180 }}>
      <MetaText size='xs'>{formatInstant(datum.t)}</MetaText>
      {datum.name && (
        <MetaText size='xs' mb={4}>
          {datum.name}
        </MetaText>
      )}
      <Stack gap={2}>
        {row(xLabel, formatValue(datum.x, xDecimals, xUnit, xRange))}
        {row(yLabel, formatValue(datum.y, yDecimals, yUnit, yRange))}
      </Stack>
    </Paper>
  );
}

export default RelationChart;
