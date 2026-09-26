import { t } from '@lingui/core/macro';
import { ScatterChart } from '@mantine/charts';
import { Paper, Stack, Text } from '@mantine/core';
import { useMemo } from 'react';

import { formatTick, formatValue } from './format';
import type { RelationPoint } from './series';

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
              x: (value: number) => formatValue(value, xDecimals),
              y: (value: number) => formatValue(value, yDecimals)
            }}
            xAxisProps={{ domain: ['auto', 'auto'] }}
            yAxisProps={{ domain: ['auto', 'auto'], width: 56 }}
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
