import { t } from '@lingui/core/macro';
import { LineChart, type LineChartSeries } from '@mantine/charts';
import {
  ActionIcon,
  Box,
  Button,
  Chip,
  Group,
  Modal,
  Paper,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import { IconMaximize, IconZoomIn } from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { ReferenceArea } from 'recharts';

import { formatInstant, formatTick, formatValue } from './format';
import type { ChartRow } from './series';

/**
 * One drawn line: which column of the rows, how to name and colour it, and
 * how to print its values. Units differ per line, so the tooltip asks each.
 */
export interface TrendSeries {
  key: string;
  label: string;
  color: string;
  unit: string;
  decimals: number;
  yAxisId?: 'left' | 'right';
  /** Draw this line as steps: a status or a count changes, it does not drift. */
  stepped?: boolean;
}

export interface ReferenceLine {
  y: number;
  label: string;
  color: string;
}

export interface TrendChartProps {
  rows: ChartRow[];
  series: TrendSeries[];
  windowStart: number;
  windowEnd: number;
  windowSeconds: number;
  height?: number;
  /** Axis unit labels; a mixed-unit chart leaves them off and lets the tooltip say. */
  unit?: string;
  rightUnit?: string;
  referenceLines?: ReferenceLine[];
  /** Charts sharing an id share a crosshair: hover one, read them all. */
  syncId?: string;
  title?: string;
  /** Draw as steps: status traces, counts - things that change, not drift. */
  stepped?: boolean;
  /** When given, a brush appears and a selection can be zoomed into. */
  onZoom?: (from: number, to: number) => void;
  /** Offer per-line show/hide when there are enough lines to want it. */
  toggleable?: boolean;
  emptyText?: string;
}

/**
 * Shared time-series chart for the Performance tab.
 *
 * Every trend on the page is this component, so every trend behaves the same
 * way: a numeric time axis pinned to the requested window, no dots, no curve
 * smoothing, a break wherever the source had no reading, one crosshair across
 * every chart that shares a `syncId`, and a tooltip that prints each line in
 * its own unit at its own precision.
 *
 * Zoom is a refetch, not a rescale. Dragging across a chart highlights a
 * range and offers it as "Zoom to <range>"; taking it asks the server for that
 * window, which comes back at finer resolution - down to every five-second
 * reading when the window is short enough. Stretching the same points across
 * a narrower axis would show nothing new and imply detail that was never read.
 * (A Recharts brush would do the same job, but a brush on every chart that
 * shares a crosshair makes the charts re-synchronise each other without end.)
 */
export function TrendChart(props: Readonly<TrendChartProps>) {
  const [fullscreen, setFullscreen] = useState(false);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const visible = useMemo(
    () => props.series.filter((s) => !hidden.has(s.key)),
    [props.series, hidden]
  );

  const toggle = (key: string) =>
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else if (next.size < props.series.length - 1) {
        // Never hide the last line: an empty chart says nothing.
        next.add(key);
      }
      return next;
    });

  const header = (
    <Group justify='space-between' align='flex-start' wrap='nowrap' gap='xs'>
      <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
        {props.title && (
          <Text size='sm' fw={600}>
            {props.title}
          </Text>
        )}
        {props.toggleable && props.series.length > 1 && (
          <Group gap={4} wrap='wrap'>
            {props.series.map((s) => (
              <Chip
                key={s.key}
                size='xs'
                variant='outline'
                color={s.color}
                checked={!hidden.has(s.key)}
                onChange={() => toggle(s.key)}
              >
                {s.label}
              </Chip>
            ))}
          </Group>
        )}
      </Stack>
      <Tooltip label={t`Open full screen`}>
        <ActionIcon
          variant='subtle'
          color='gray'
          size='sm'
          aria-label={t`Open full screen`}
          onClick={() => setFullscreen(true)}
        >
          <IconMaximize size={16} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );

  return (
    <Stack gap={6}>
      {header}
      <ChartBody {...props} series={visible} />
      <Modal
        opened={fullscreen}
        onClose={() => setFullscreen(false)}
        fullScreen
        title={props.title ?? t`Trend`}
      >
        <ChartBody
          {...props}
          series={visible}
          height={Math.max(420, window.innerHeight - 200)}
        />
      </Modal>
    </Stack>
  );
}

function ChartBody({
  rows,
  series,
  windowStart,
  windowEnd,
  windowSeconds,
  height = 240,
  unit,
  rightUnit,
  referenceLines,
  syncId,
  stepped,
  onZoom,
  toggleable,
  emptyText
}: Readonly<TrendChartProps>) {
  // A drag across the plot, in axis instants. `dragging` distinguishes a drag
  // in progress from a finished selection waiting for its button.
  const [selection, setSelection] = useState<{
    start: number;
    end: number;
  } | null>(null);
  const [dragging, setDragging] = useState(false);

  const plotted = useMemo(
    () => rows.some((row) => series.some((s) => row[s.key] !== null)),
    [rows, series]
  );

  const anyLeftAxis = series.some((s) => s.yAxisId !== 'right');
  const mantineSeries: LineChartSeries[] = useMemo(
    () =>
      series.map((s) => ({
        name: s.key,
        label: s.label,
        color: s.color,
        // Mantine's left axis is Recharts' default axis and has no id; only
        // the right one is named. A series told 'left' binds to nothing.
        yAxisId: s.yAxisId === 'right' && anyLeftAxis ? 'right' : undefined,
        curveType: s.stepped || stepped ? 'stepAfter' : 'linear'
      })),
    [series, stepped, anyLeftAxis]
  );

  const byKey = useMemo(() => new Map(series.map((s) => [s.key, s])), [series]);
  // Each line's spread over the window, so the tooltip prints enough decimals
  // to tell the line's own movement apart.
  const rangeByKey = useMemo(() => {
    const map = new Map<string, number>();
    for (const s of series) {
      let min = Number.POSITIVE_INFINITY;
      let max = Number.NEGATIVE_INFINITY;
      for (const row of rows) {
        const v = row[s.key];
        if (v !== null && v !== undefined && Number.isFinite(v)) {
          if (v < min) min = v;
          if (v > max) max = v;
        }
      }
      map.set(s.key, Number.isFinite(max - min) ? max - min : 0);
    }
    return map;
  }, [series, rows]);
  // A right axis only makes sense opposite a left one. When every line asked
  // for the right axis - the left-axis parameter is not mapped on this pump -
  // they take the left axis instead, rather than leaving it labelled and empty.
  const withRight = anyLeftAxis && series.some((s) => s.yAxisId === 'right');

  const zoomRange = useMemo(() => {
    if (!selection || dragging) return null;
    const from = Math.min(selection.start, selection.end);
    const to = Math.max(selection.start, selection.end);
    if (to - from < 30 * 1000) return null;
    // A selection that is most of the window is not a zoom.
    if (to - from > 0.9 * (windowEnd - windowStart)) return null;
    return { from, to };
  }, [selection, dragging, windowStart, windowEnd]);

  // Recharts hands the hovered x value as `activeLabel`; on a numeric axis
  // that is the instant under the cursor.
  const instantOf = (state: unknown): number | null => {
    const label = (state as { activeLabel?: unknown } | null)?.activeLabel;
    return typeof label === 'number' && Number.isFinite(label) ? label : null;
  };

  if (!plotted) {
    return (
      <Paper withBorder radius='sm' p='md' h={height}>
        <Text size='sm' c='dimmed'>
          {emptyText ?? t`No readings in this window.`}
        </Text>
      </Paper>
    );
  }

  return (
    <Box>
      <LineChart
        h={height}
        data={rows}
        dataKey='t'
        series={mantineSeries}
        curveType={stepped ? 'stepAfter' : 'linear'}
        withDots={false}
        connectNulls={false}
        strokeWidth={1.5}
        // The toggle chips already name and colour every line; a legend as
        // well wraps under a narrow card and runs into whatever follows.
        withLegend={!toggleable && series.length > 1 && series.length <= 8}
        legendProps={{ verticalAlign: 'bottom', height: 28 }}
        yAxisLabel={(anyLeftAxis ? unit : rightUnit) || undefined}
        withRightYAxis={withRight}
        rightYAxisLabel={withRight ? rightUnit || undefined : undefined}
        yAxisProps={{
          width: 56,
          allowDataOverflow: false,
          domain: ['auto', 'auto']
        }}
        rightYAxisProps={{
          width: 56,
          allowDataOverflow: false,
          domain: ['auto', 'auto']
        }}
        xAxisProps={{
          type: 'number',
          scale: 'time',
          domain: [windowStart, windowEnd],
          allowDataOverflow: true,
          tickFormatter: (value: number) => formatTick(value, windowSeconds),
          minTickGap: 48
        }}
        referenceLines={referenceLines?.map((line) => ({
          y: line.y,
          label: line.label,
          color: line.color
        }))}
        lineChartProps={{
          syncId,
          syncMethod: 'value',
          onMouseDown: (state: unknown) => {
            const at = instantOf(state);
            if (!onZoom || at === null) return;
            setSelection({ start: at, end: at });
            setDragging(true);
          },
          onMouseMove: (state: unknown) => {
            const at = instantOf(state);
            if (!dragging || at === null) return;
            setSelection((current) =>
              current ? { ...current, end: at } : current
            );
          },
          onMouseUp: () => setDragging(false),
          onMouseLeave: () => setDragging(false)
        }}
        tooltipAnimationDuration={0}
        tooltipProps={{
          isAnimationActive: false,
          content: ({ label, payload }) => (
            <TrendTooltip
              instant={typeof label === 'number' ? label : null}
              payload={(payload as any[]) ?? []}
              byKey={byKey}
              rangeByKey={rangeByKey}
            />
          )
        }}
      >
        {selection && selection.start !== selection.end && (
          <ReferenceArea
            x1={Math.min(selection.start, selection.end)}
            x2={Math.max(selection.start, selection.end)}
            fill='var(--mantine-color-blue-5)'
            fillOpacity={0.12}
            stroke='var(--mantine-color-blue-5)'
            strokeOpacity={0.5}
          />
        )}
      </LineChart>
      {onZoom && zoomRange && (
        <Group justify='flex-end' mt={4} gap='xs'>
          <Button
            size='compact-xs'
            variant='subtle'
            color='gray'
            onClick={() => setSelection(null)}
          >
            {t`Clear selection`}
          </Button>
          <Button
            size='compact-xs'
            variant='light'
            leftSection={<IconZoomIn size={14} />}
            onClick={() => {
              onZoom(zoomRange.from, zoomRange.to);
              setSelection(null);
            }}
          >
            {t`Zoom to ${formatTick(zoomRange.from, windowSeconds)} – ${formatTick(zoomRange.to, windowSeconds)}`}
          </Button>
        </Group>
      )}
    </Box>
  );
}

/**
 * Every line at the hovered instant, each in its own unit.
 *
 * A row with a `null` for a line is a gap, and the tooltip says "no reading"
 * rather than leaving the line out - the absence is the information.
 */
function TrendTooltip({
  instant,
  payload,
  byKey,
  rangeByKey
}: Readonly<{
  instant: number | null;
  payload: {
    dataKey?: string | number;
    name?: string;
    value?: unknown;
    color?: string;
  }[];
  byKey: Map<string, TrendSeries>;
  rangeByKey: Map<string, number>;
}>) {
  if (instant === null || payload.length === 0) {
    return null;
  }
  return (
    <Paper withBorder shadow='sm' radius='sm' p='xs' style={{ minWidth: 180 }}>
      <Text size='xs' c='dimmed' mb={4}>
        {formatInstant(instant)}
      </Text>
      <Stack gap={2}>
        {payload.map((item) => {
          const key = String(item.dataKey ?? item.name ?? '');
          const meta = byKey.get(key);
          const value =
            typeof item.value === 'number' && Number.isFinite(item.value)
              ? item.value
              : null;
          return (
            <Group key={key} justify='space-between' gap='md' wrap='nowrap'>
              <Group gap={6} wrap='nowrap'>
                <Box
                  w={8}
                  h={8}
                  style={{
                    borderRadius: 2,
                    background: `var(--mantine-color-${(meta?.color ?? 'gray.6').replace('.', '-')})`
                  }}
                />
                <Text size='xs'>{meta?.label ?? key}</Text>
              </Group>
              <Text
                size='xs'
                fw={600}
                c={value === null ? 'dimmed' : undefined}
              >
                {value === null
                  ? t`no reading`
                  : formatValue(
                      value,
                      meta?.decimals ?? 2,
                      meta?.unit,
                      rangeByKey.get(key)
                    )}
              </Text>
            </Group>
          );
        })}
      </Stack>
    </Paper>
  );
}

/**
 * Colours for a family of lines. One hue family, cycled through shades so a
 * dozen winding sensors read as one family rather than a rainbow, with a
 * second hue interleaved once the first runs out of distinguishable shades.
 */
export function familyColors(
  count: number,
  hues: string[] = ['blue']
): string[] {
  const shades = [6, 8, 4, 9, 5, 7, 3];
  const colors: string[] = [];
  for (let i = 0; i < count; i++) {
    const hue = hues[Math.floor(i / shades.length) % hues.length];
    colors.push(`${hue}.${shades[i % shades.length]}`);
  }
  return colors;
}

export default TrendChart;
