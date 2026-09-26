import { t } from '@lingui/core/macro';
import {
  Box,
  Group,
  Stack,
  Text,
  useComputedColorScheme,
  useMantineTheme
} from '@mantine/core';
import { useElementSize } from '@mantine/hooks';
import { useMemo } from 'react';

import { formatTick, formatValue, timeTicks } from './format';
import { type HeatmapMatrix, limitState } from './series';

//: Row labels are measured, not assumed; these only bound the result, so one
//: long sensor name cannot eat the grid and a short one cannot strand it.
const MIN_LABEL_WIDTH = 110;
const MAX_LABEL_WIDTH = 260;
const LABEL_GUTTER = 8;
const LABEL_FONT = '11px system-ui, sans-serif';
const ROW_HEIGHT = 18;
const AXIS_HEIGHT = 18;
const GAP = 1;

/**
 * Width of a label in the 11px font the heatmap - and the bar chart beside
 * it - draws its sensor names in.
 *
 * SVG text does not wrap or truncate: a label wider than the space reserved
 * for it is simply drawn outside the picture and clipped from its *front*,
 * which silently renames a sensor - "Motor NDE bearing vibration 1" becomes
 * "DE bearing vibration 1", the name of a different channel on the same pump.
 * So the width is measured and the text, if it still does not fit, is cut at
 * the end where a reader can see that it was cut.
 */
let canvasContext: CanvasRenderingContext2D | null | undefined;

export function measureText(text: string): number {
  if (canvasContext === undefined) {
    canvasContext = document.createElement('canvas').getContext('2d');
    if (canvasContext) canvasContext.font = LABEL_FONT;
  }
  // Without a canvas - jsdom, say - fall back to a width per character that
  // over-estimates slightly, so a label is trimmed rather than clipped.
  return canvasContext?.measureText(text).width ?? text.length * 6.2;
}

function fitText(text: string, maxWidth: number): string {
  if (measureText(text) <= maxWidth) return text;
  let cut = text.length;
  while (cut > 1 && measureText(`${text.slice(0, cut)}…`) > maxWidth) cut--;
  return `${text.slice(0, cut)}…`;
}

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
  const scheme = useComputedColorScheme('light');
  const { ref, width } = useElementSize<HTMLDivElement>();

  const palette = theme.colors[hue] ?? theme.colors.orange;
  // Which end of the ramp is "low" depends on what the card is made of. On
  // white, low is pale and high is saturated; on a dark card those same fills
  // invert the meaning - the coolest sensors become the brightest rows - so
  // low there is the shade nearest the background instead. Either way, further
  // from the card means higher, which is what "a sensor running hot is a
  // bright row" promises.
  const [rampLow, rampHigh] = scheme === 'dark' ? [9, 3] : [1, 8];

  // The row label is the sensor's name and nothing else. Appending the
  // window's last reading put a second, unlabelled number beside a name -
  // read as a current value, and so as a contradiction of the bar chart
  // beside it, which shows the signal's own current reading. The fact keeps
  // its place on hover, where it can be named.
  const labels = useMemo(
    () => matrix.rows.map((row) => row.label),
    [matrix.rows]
  );
  const rowTitles = useMemo(
    () =>
      matrix.rows.map((row) =>
        row.last !== null
          ? t`${row.label} · last reading in window ${formatValue(row.last, decimals, unit, matrix.max - matrix.min)}`
          : row.label
      ),
    [matrix.rows, matrix.max, matrix.min, decimals, unit]
  );
  const labelWidth = useMemo(
    () =>
      Math.min(
        MAX_LABEL_WIDTH,
        Math.max(
          MIN_LABEL_WIDTH,
          Math.max(0, ...labels.map(measureText)) + LABEL_GUTTER * 2
        )
      ),
    [labels]
  );

  const gridWidth = Math.max(120, width - labelWidth);
  const columns = matrix.columns.length;
  const cellWidth = gridWidth / columns;
  const height = matrix.rows.length * ROW_HEIGHT + AXIS_HEIGHT;

  const shade = useMemo(() => {
    const span = matrix.max - matrix.min;
    return (value: number) => {
      if (span === 0)
        return palette[rampLow + Math.round((rampHigh - rampLow) / 2)];
      const norm = Math.max(0, Math.min(1, (value - matrix.min) / span));
      return palette[rampLow + Math.round(norm * (rampHigh - rampLow))];
    };
  }, [matrix.min, matrix.max, palette, rampLow, rampHigh]);

  // A handful of time labels along the bottom, at the same instants the trend
  // chart in the same card prints: every nth column start gave 21:11, 21:21,
  // 21:31 under a chart ticking 21:15, 21:20, 21:25, so the two axes of one
  // card named different times.
  const axisStart = matrix.columns[0]?.start ?? 0;
  const axisEnd = matrix.columns[columns - 1]?.end ?? axisStart;
  const ticks = useMemo(
    () =>
      axisEnd > axisStart
        ? timeTicks(axisStart, axisEnd, Math.max(2, Math.floor(gridWidth / 90)))
        : [],
    [axisStart, axisEnd, gridWidth]
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
              x={labelWidth - LABEL_GUTTER}
              y={ROW_HEIGHT / 2 + 4}
              fontSize={11}
              textAnchor='end'
              fill='var(--mantine-color-text)'
            >
              {fitText(labels[r], labelWidth - LABEL_GUTTER * 2)}
              <title>{rowTitles[r]}</title>
            </text>
            {row.cells.map((cell, c) => {
              const column = matrix.columns[c];
              const state = limitState(cell, row.limits);
              const outline = stateColor[state];
              // A cell resting on one reading is that reading, not a mean of
              // one: "mean of 1 readings" claims an average that averages
              // nothing.
              const title =
                cell === null
                  ? t`${row.label} · ${formatTick(column.start, windowSeconds)}–${formatTick(column.end, windowSeconds)} · no reading`
                  : row.counts[c] === 1
                    ? t`${row.label} · ${formatTick(column.start, windowSeconds)}–${formatTick(column.end, windowSeconds)} · ${formatValue(cell, decimals, unit, matrix.max - matrix.min)} (one reading)`
                    : t`${row.label} · ${formatTick(column.start, windowSeconds)}–${formatTick(column.end, windowSeconds)} · ${formatValue(cell, decimals, unit, matrix.max - matrix.min)} (mean of ${row.counts[c]} readings)`;
              return (
                <rect
                  key={c}
                  x={labelWidth + c * cellWidth + GAP / 2}
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
          {ticks.map((at) => {
            const text = formatTick(at, windowSeconds);
            const offset = (at - axisStart) / (axisEnd - axisStart);
            const x = labelWidth + offset * gridWidth;
            // A centred label near either end would hang off the grid.
            const half = measureText(text) / 2;
            if (x - half < labelWidth || x + half > labelWidth + gridWidth) {
              return null;
            }
            return (
              <text
                key={at}
                x={x}
                y={13}
                fontSize={10}
                textAnchor='middle'
                fill='var(--mantine-color-dimmed)'
              >
                {text}
              </text>
            );
          })}
        </g>
      </Box>
      <Group gap='xs' justify='flex-end'>
        <Text size='xs' c='dimmed'>
          {t`Scale spans the observed range:`}
        </Text>
        <Text size='xs'>
          {formatValue(matrix.min, decimals, unit, matrix.max - matrix.min)}
        </Text>
        <Box
          w={96}
          h={8}
          style={{
            borderRadius: 2,
            background: `linear-gradient(90deg, ${palette[rampLow]}, ${palette[rampHigh]})`
          }}
        />
        <Text size='xs'>
          {formatValue(matrix.max, decimals, unit, matrix.max - matrix.min)}
        </Text>
        <Text size='xs' c='dimmed'>
          {t`· each cell is the mean of the readings in its column`}
        </Text>
      </Group>
    </Stack>
  );
}

export default SensorHeatmap;
