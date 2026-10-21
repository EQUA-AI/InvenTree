import { t } from '@lingui/core/macro';
import { Box, Paper, SimpleGrid, Stack, Text } from '@mantine/core';
import { Children, type ReactNode, isValidElement } from 'react';
import monitoring from '../../monitoring.module.css';

import type { SeriesEntry } from '@lib/types/MachineHealth';

import type { WindowInfo } from '../SensorGroupCard';
import {
  ChartTitleProvider,
  TrendChart,
  type TrendSeries
} from '../TrendChart';
import type { ResolvedParameter } from '../resolve';
import { type ChartRow, alignRows, gapThresholdMs } from '../series';
import { splitAxes, unitText } from '../units';

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
    bindingIds: [parameter.signal.binding_id],
    externalKeys: [parameter.signal.external_key],
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

/**
 * The grid a section lays its cards out on.
 *
 * Two things a plain SimpleGrid does not do here. Which cards exist depends on
 * the tags a machine carries, so an odd last card would sit at half width
 * beside a blank half; it spans the rest of its row instead. And cards are not
 * stretched to the tallest in their row: where one card carries a comparison
 * bar chart its neighbours do not, stretching left a quarter of a page of white
 * inside their borders, which reads as a chart that failed to draw. Each card
 * is now as tall as what it has to say, and their tops - where the titles and
 * the chips are - still line up.
 *
 * At base the grid is one column, where both rules are already no-ops.
 */
export function CardGrid({
  cols = 2,
  children
}: Readonly<{ cols?: number; children: ReactNode }>) {
  // toArray drops the `false` that an absent card's guard leaves behind.
  const cards = Children.toArray(children);
  const spanLast = cards.length % cols === 1;
  return (
    <SimpleGrid
      cols={{ base: 1, lg: cols }}
      spacing='md'
      style={{ alignItems: 'start' }}
    >
      {cards.map((card, index) =>
        spanLast && index === cards.length - 1 ? (
          <Box
            key={isValidElement(card) ? card.key : String(card)}
            style={{ gridColumn: '1 / -1' }}
          >
            {card}
          </Box>
        ) : (
          card
        )
      )}
    </SimpleGrid>
  );
}

/** A titled card holding one trend, the way every section draws one. */
export function ChartCard({
  title,
  description,
  children
}: Readonly<{ title: string; description?: string; children: ReactNode }>) {
  return (
    <Paper withBorder p='md'>
      <Stack gap='xs'>
        <Stack gap={2}>
          <Text fw={600}>{title}</Text>
          {description && (
            <Text size='xs' className={monitoring.meta}>
              {description}
            </Text>
          )}
        </Stack>
        <ChartTitleProvider title={title}>{children}</ChartTitleProvider>
      </Stack>
    </Paper>
  );
}

/**
 * A trend of a few named parameters, on up to two axes.
 *
 * Each axis carries one scale (R6 A19.7). A card pairs families by meaning -
 * current with voltage, pressure with valve position - and their units by
 * assumption: a line-to-line voltage in kV and phase voltages in V shared
 * one right axis labelled with the first unit. Lines whose unit does not
 * match their axis's first scale are drawn on their own trend below, in the
 * same card, and the card says why. Nothing is rescaled.
 */
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
    <ScaledTrend
      rows={rows}
      lines={lines}
      window={window}
      syncId={syncId}
      onZoom={onZoom}
      unit={unit}
      rightUnit={rightUnit}
      height={height}
    />
  );
}

/** A trend over prepared rows, one scale per axis; see `ParameterTrend`. */
export function ScaledTrend({
  rows,
  lines,
  window,
  syncId,
  onZoom,
  unit,
  rightUnit,
  height,
  toggleable
}: Readonly<{
  rows: ChartRow[];
  lines: TrendSeries[];
  window: WindowInfo;
  syncId: string;
  onZoom?: (from: number, to: number) => void;
  unit?: string;
  rightUnit?: string;
  height?: number;
  /** Per-line chips instead of a legend; by default past two lines. */
  toggleable?: boolean;
}>) {
  const split = splitAxes(lines);
  const main = (
    <TrendChart
      rows={rows}
      series={split.main}
      windowStart={window.start}
      windowEnd={window.end}
      windowSeconds={window.seconds}
      unit={split.leftWhole ? unit : split.leftUnits}
      rightUnit={split.rightWhole ? rightUnit : split.rightUnits}
      syncId={syncId}
      onZoom={onZoom}
      toggleable={toggleable ?? split.main.length > 2}
      height={height}
    />
  );
  if (split.apart.length === 0) return main;
  return (
    <Stack gap='sm' data-units-split={split.apart.length}>
      {main}
      {split.apart.map(({ axis, group }) => {
        const own = unitText(group) || t`no unit`;
        const shared =
          (axis === 'left' ? split.leftUnits : split.rightUnits) || t`no unit`;
        return (
          <Stack gap={4} key={`${axis}:${group.key}`}>
            <Text size='xs' className={monitoring.meta} data-units-apart={own}>
              {t`Drawn apart: ${group.items.map((l) => l.label).join(', ')} in ${own}, not ${shared}.`}
            </Text>
            <TrendChart
              rows={rows}
              series={group.items.map((line) => ({
                ...line,
                yAxisId: 'left' as const
              }))}
              windowStart={window.start}
              windowEnd={window.end}
              windowSeconds={window.seconds}
              unit={unitText(group)}
              syncId={syncId}
              onZoom={onZoom}
              toggleable={toggleable ?? group.items.length > 2}
              height={height ?? 200}
            />
          </Stack>
        );
      })}
    </Stack>
  );
}
