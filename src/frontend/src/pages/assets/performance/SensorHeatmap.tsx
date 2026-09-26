import { t } from '@lingui/core/macro';
import { Box, Group, Stack, Text, useMantineTheme } from '@mantine/core';
import { useElementSize } from '@mantine/hooks';
import { useMemo } from 'react';

import { formatTick, formatValue } from './format';
import { type HeatmapMatrix, limitState } from './series';

const LABEL_WIDTH = 150;
const ROW_HEIGHT = 18;
const AXIS_HEIGHT = 18;
const GAP = 1;

export interface SensorHeatmapProps {
  matrix: HeatmapMatrix;
  unit: string;
  decimals: number;
  windowSeconds: number;
  /** Colour family for the scale. Warm for temperature, cool for the rest. */
  hue?: string;
}

/**
 * Sensors down, time across, colour for value.
 *
 * The point of a heatmap here is that twelve winding sensors or ten thrust
 * pads can be read at a glance: a sensor running hot is a bright row, a
 * change in the window is a change of shade left to right. The scale spans the
 * matrix's own observed range - it says so, with the two ends printed - so it
 * is a comparison of sensors against each other, never a verdict. Where a
 * sensor has configured limits and a cell crosses one, that cell is outlined
 * in the state's colour; that is the only place a threshold enters.
 *
 * Plain SVG rather than a chart library: a grid of rectangles with native
 * tooltips is lighter than five hundred React tooltips and needs nothing more.
 */
export function SensorHeatmap({
  matrix,
  unit,
  decimals,
  windowSeconds,
  hue = 'orange'
}: Readonly<SensorHeatmapProps>) {
  const theme = useMantineTheme();
  const { ref, width } = useElementSize<HTMLDivElement>();

  const palette = theme.colors[hue] ?? theme.colors.orange;
  const gridWidth = Math.max(120, width - LABEL_WIDTH);
  const columns = matrix.columns.length;
  const cellWidth = gridWidth / columns;
  const height = matrix.rows.length * ROW_HEIGHT + AXIS_HEIGHT;

  const shade = useMemo(() => {
    const span = matrix.max - matrix.min;
    return (value: number) => {
      if (span === 0) return palette[4];
      const norm = Math.max(0, Math.min(1, (value - matrix.min) / span));
      return palette[1 + Math.round(norm * 7)];
    };
  }, [matrix.min, matrix.max, palette]);

  // A handful of time labels along the bottom, never one per column.
  const labelEvery = Math.max(
    1,
    Math.ceil(columns / Math.max(2, Math.floor(gridWidth / 90)))
  );

  const stateColor: Record<string, string> = {
    critical: theme.colors.red[7],
    warning: theme.colors.yellow[6]
  };

  return (
    <Stack gap={4} ref={ref}>
      <Box
        component='svg'
        width='100%'
        height={height}
        role='img'
        aria-label={t`Sensor heatmap`}
      >
        {matrix.rows.map((row, r) => (
          <g key={row.key} transform={`translate(0, ${r * ROW_HEIGHT})`}>
            <text
              x={LABEL_WIDTH - 8}
              y={ROW_HEIGHT / 2 + 4}
              fontSize={11}
              textAnchor='end'
              fill='var(--mantine-color-text)'
            >
              {row.label}
              {row.last !== null ? `  ${formatValue(row.last, decimals)}` : ''}
            </text>
            {row.cells.map((cell, c) => {
              const column = matrix.columns[c];
              const state = limitState(cell, row.limits);
              const outline = stateColor[state];
              const title =
                cell === null
                  ? t`${row.label} · ${formatTick(column.start, windowSeconds)}–${formatTick(column.end, windowSeconds)} · no reading`
                  : t`${row.label} · ${formatTick(column.start, windowSeconds)}–${formatTick(column.end, windowSeconds)} · ${formatValue(cell, decimals, unit)} (mean of ${row.counts[c]} readings)`;
              return (
                <rect
                  key={c}
                  x={LABEL_WIDTH + c * cellWidth + GAP / 2}
                  y={GAP}
                  width={Math.max(0.5, cellWidth - GAP)}
                  height={ROW_HEIGHT - GAP * 2}
                  rx={1}
                  fill={
                    cell === null
                      ? 'var(--mantine-color-default-border)'
                      : shade(cell)
                  }
                  fillOpacity={cell === null ? 0.35 : 1}
                  stroke={outline}
                  strokeWidth={outline ? 1.5 : 0}
                >
                  <title>{title}</title>
                </rect>
              );
            })}
          </g>
        ))}
        <g transform={`translate(0, ${matrix.rows.length * ROW_HEIGHT})`}>
          {matrix.columns.map((column, c) =>
            c % labelEvery === 0 ? (
              <text
                key={c}
                x={LABEL_WIDTH + c * cellWidth}
                y={13}
                fontSize={10}
                fill='var(--mantine-color-dimmed)'
              >
                {formatTick(column.start, windowSeconds)}
              </text>
            ) : null
          )}
        </g>
      </Box>
      <Group gap='xs' justify='flex-end'>
        <Text size='xs' c='dimmed'>
          {t`Scale spans the observed range:`}
        </Text>
        <Text size='xs'>{formatValue(matrix.min, decimals, unit)}</Text>
        <Box
          w={96}
          h={8}
          style={{
            borderRadius: 2,
            background: `linear-gradient(90deg, ${palette[1]}, ${palette[8]})`
          }}
        />
        <Text size='xs'>{formatValue(matrix.max, decimals, unit)}</Text>
        <Text size='xs' c='dimmed'>
          {t`· each cell is the mean of the readings in its column`}
        </Text>
      </Group>
    </Stack>
  );
}

export default SensorHeatmap;
