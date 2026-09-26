import { t } from '@lingui/core/macro';
import { ScatterChart } from '@mantine/charts';
import { Paper, Stack, Text } from '@mantine/core';
import { useMemo } from 'react';

import { formatTick, formatValue } from './format';
import { type RelationPoint, axisDomain } from './series';

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
 * Each point is one snapshot in which both were read. Colour darkens toward
 * the present, in three bands, so a drift over the window shows as a drift
 * across the plot. The titles say "relationship" and the caption says what a
 * point is; nothing here claims one parameter drives the other.
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

  // As wide as the longest tick it will print, for the same reason the trend
  // charts measure theirs: a fixed 56px wraps every pressure tick onto two
  // lines.
  const yAxisWidth = useMemo(() => {
    const longest = Math.max(
      ...[extent.y.min, extent.y.max]
        .filter((v) => Number.isFinite(v))
        .map(
          (v) => formatValue(v, yDecimals, undefined, extent.y.range).length
        ),
      1
    );
    return 14 + 7 * Math.min(longest, 10);
  }, [extent.y, yDecimals]);

  const bands = useMemo(() => {
    if (points.length === 0) return [];
    const third = Math.max(1, Math.ceil(points.length / 3));
    const slices = [
      points.slice(0, third),
      points.slice(third, third * 2),
      points.slice(third * 2)
    ].filter((slice) => slice.length > 0);
    const colors = ['gray.4', 'blue.4', 'blue.8'];
    return slices.map((slice, i) => ({
      color: colors[i + (3 - slices.length)] ?? 'blue.6',
      name: t`${formatTick(slice[0].t, windowSeconds)} – ${formatTick(slice[slice.length - 1].t, windowSeconds)}`,
      data: slice.map((p) => ({ x: p.x, y: p.y }))
    }));
  }, [points, windowSeconds]);

  return (
    <Paper withBorder radius='md' p='sm'>
      <Stack gap={4}>
        <Text size='sm' fw={600}>
          {title}
        </Text>
        {points.length < 3 ? (
          <Text size='sm' c='dimmed' py='lg'>
            {t`Too few paired readings in this window to show a relationship.`}
          </Text>
        ) : (
          <ScatterChart
            h={height}
            data={bands}
            dataKey={{ x: 'x', y: 'y' }}
            xAxisLabel={xUnit ? `${xLabel} (${xUnit})` : xLabel}
            yAxisLabel={yUnit ? `${yLabel} (${yUnit})` : yLabel}
            labels={{ x: xLabel, y: yLabel }}
            unit={{ x: xUnit ? ` ${xUnit}` : '', y: yUnit ? ` ${yUnit}` : '' }}
            valueFormatter={{
              x: (value: number) =>
                formatValue(value, xDecimals, undefined, extent.x.range),
              y: (value: number) =>
                formatValue(value, yDecimals, undefined, extent.y.range)
            }}
            xAxisProps={{ domain: axisDomain(extent.x.min, extent.x.max) }}
            yAxisProps={{
              domain: axisDomain(extent.y.min, extent.y.max),
              width: yAxisWidth
            }}
            withLegend
            legendProps={{ verticalAlign: 'bottom', height: 28 }}
            scatterProps={{ isAnimationActive: false }}
            tooltipAnimationDuration={0}
          />
        )}
        <Text size='xs' c='dimmed'>
          {t`Each point is one snapshot in which both were read; colour darkens toward the present. A pattern here is an operating pattern, not a cause.`}
        </Text>
      </Stack>
    </Paper>
  );
}

export default RelationChart;
