import { Paper, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import type { SeriesEntry } from '@lib/types/MachineHealth';

import type { WindowInfo } from '../SensorGroupCard';
import { TrendChart, type TrendSeries } from '../TrendChart';
import type { ResolvedParameter } from '../resolve';
import { type ChartRow, alignRows, gapThresholdMs } from '../series';

/** What every section receives: the machine's parameters and their history. */
export interface SectionProps {
  parameters: ResolvedParameter[];
  series: Map<number, SeriesEntry>;
  window: WindowInfo;
  syncId: string;
  onZoom: (from: number, to: number) => void;
}

export function lineFor(
  parameter: ResolvedParameter,
  color: string,
  yAxisId: 'left' | 'right' = 'left',
  stepped = false
): TrendSeries {
  return {
    key: parameter.id,
    label: parameter.label,
    color,
    unit: parameter.signal.unit,
    decimals: parameter.definition.decimals,
    yAxisId,
    stepped
  };
}

export function rowsFor(
  parameters: readonly ResolvedParameter[],
  series: Map<number, SeriesEntry>,
  window: WindowInfo
): ChartRow[] {
  return alignRows(
    parameters.map((p) => ({
      key: p.id,
      entry: series.get(p.signal.binding_id)
    })),
    gapThresholdMs(window.resolutionSeconds)
  );
}

/** A titled card holding one trend, the way every section draws one. */
export function ChartCard({
  title,
  description,
  children
}: Readonly<{ title: string; description?: string; children: ReactNode }>) {
  return (
    <Paper withBorder radius='md' p='md'>
      <Stack gap='xs'>
        <Stack gap={2}>
          <Text fw={600}>{title}</Text>
          {description && (
            <Text size='xs' c='dimmed'>
              {description}
            </Text>
          )}
        </Stack>
        {children}
      </Stack>
    </Paper>
  );
}

/** A trend of a few named parameters, on up to two axes. */
export function ParameterTrend({
  parameters,
  lines,
  series,
  window,
  syncId,
  onZoom,
  unit,
  rightUnit,
  height
}: Readonly<{
  parameters: ResolvedParameter[];
  lines: TrendSeries[];
  series: Map<number, SeriesEntry>;
  window: WindowInfo;
  syncId: string;
  onZoom?: (from: number, to: number) => void;
  unit?: string;
  rightUnit?: string;
  height?: number;
}>) {
  const rows = rowsFor(parameters, series, window);
  return (
    <TrendChart
      rows={rows}
      series={lines}
      windowStart={window.start}
      windowEnd={window.end}
      windowSeconds={window.seconds}
      unit={unit}
      rightUnit={rightUnit}
      syncId={syncId}
      onZoom={onZoom}
      toggleable={lines.length > 2}
      height={height}
    />
  );
}
