import { t } from '@lingui/core/macro';
import { LineChart, type LineChartSeries } from '@mantine/charts';
import {
  ActionIcon,
  Box,
  Button,
  Chip,
  Group,
  type MantineTheme,
  Modal,
  Paper,
  Stack,
  Text,
  Tooltip,
  getThemeColor,
  useComputedColorScheme,
  useMantineTheme
} from '@mantine/core';
import { useElementSize } from '@mantine/hooks';
import { IconMaximize, IconZoomIn } from '@tabler/icons-react';
import {
  type ReactNode,
  createContext,
  useContext,
  useMemo,
  useState
} from 'react';
import { ReferenceArea } from 'recharts';
import operational from '../health/OperationalTokens.module.css';
import { MetaText } from '../health/common';
import { useDisplayClock } from '../health/mimic/observedTime';
import {
  PerformanceTestNotice,
  usePerformanceTestAlarms,
  usePerformanceTestHistory
} from './PerformanceTestAlarms';
import { ThresholdKey } from './ThresholdKey';
import { presentationTicks } from './axisPresentation';
import { thresholdBands } from './thresholdBands';

import { useHistoryReadFailures } from './HistoryReadContext';
import {
  exerciseRows,
  toggleExerciseSeries,
  visibleExerciseSeries
} from './exerciseValues';
import {
  formatInstant,
  formatTick,
  formatValue,
  niceTicks,
  timeTicks,
  unitLabel
} from './format';
import { type ChartRow, axisDomain } from './series';

/**
 * One drawn line: which column of the rows, how to name and colour it, and
 * how to print its values. Units differ per line, so the tooltip asks each.
 */
export interface TrendSeries {
  measuredKey?: string;
  bindingIds?: readonly number[];
  externalKeys?: readonly string[];
  key: string;
  label: string;
  color: string;
  unit: string;
  decimals: number;
  yAxisId?: 'left' | 'right';
  /** Draw this line as steps: a status or a count changes, it does not drift. */
  stepped?: boolean;
  strokeWidth?: number;
  endLabel?: boolean;
  synthetic?: boolean;
  endLabelText?: string;
}

export interface ReferenceLine {
  synthetic?: boolean;
  yAxisId?: 'left' | 'right';
  direction?: 'low' | 'high';
  severity?: 'warning' | 'critical';
  y: number;
  label: string;
  color: string;
}

export interface TrendChartProps {
  rows: ChartRow[];
  measuredRows?: ChartRow[];
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
 * The name of the card a chart is drawn in.
 *
 * The maximised view replaces that card, so it is the one place that has to say
 * what it is showing. It asks the card rather than being handed the same string
 * a second time: two copies drift apart, and six of them were never written at
 * all.
 */
const ChartTitleContext = createContext<string | undefined>(undefined);

export function ChartTitleProvider({
  title,
  children
}: Readonly<{ title?: string; children: ReactNode }>) {
  return (
    <ChartTitleContext.Provider value={title}>
      {children}
    </ChartTitleContext.Provider>
  );
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
  const bindingIds = props.series.flatMap((line) => line.bindingIds ?? []);
  const tests = usePerformanceTestAlarms(bindingIds);
  const history = usePerformanceTestHistory(bindingIds);
  const failures = useHistoryReadFailures();
  const failed = props.series.filter(
    (line) =>
      line.bindingIds?.some((id) => failures.bindings.has(id)) ||
      line.externalKeys?.some((key) => failures.keys.has(key))
  );
  const failureText = failed.length
    ? t`History read failed for ${failed.map((line) => line.label).join(', ')}; coverage unknown.`
    : undefined;
  const chartProps = {
    ...props,
    emptyText:
      failureText ??
      props.emptyText ??
      (failures.missing
        ? t`History has not been returned for this window.`
        : undefined)
  };
  const [fullscreen, setFullscreen] = useState(false);
  const { ref: fullscreenControls, height: controlsHeight } = useElementSize();
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  // A chart given no title of its own is named by the card it sits in.
  const cardTitle = useContext(ChartTitleContext);

  // Every colour the chart draws passes through here, so a caller cannot hand
  // it a shade that vanishes into the card - and the chips, which are the key
  // to those colours, carry the same shade the line does.
  const theme = useMantineTheme();
  const scheme = useComputedColorScheme('light');
  const series = useMemo(
    () =>
      [
        ...props.series,
        ...history.flatMap((alarm) => {
          const measured = props.series.find(
            (line) =>
              line.bindingIds?.length === 1 &&
              line.bindingIds[0] === alarm.binding_id &&
              unitLabel(line.unit) === unitLabel(alarm.unit)
          );
          if (
            !measured ||
            alarm.unitMismatch ||
            Date.parse(alarm.triggered_at) > props.windowEnd ||
            (alarm.status === 'resolved' &&
              Date.parse(alarm.resolved_at ?? '') < props.windowStart)
          )
            return [];
          const visibleEnd = (entry: (typeof history)[number]) =>
            entry.status === 'resolved'
              ? Math.min(Date.parse(entry.resolved_at ?? ''), props.windowEnd)
              : props.windowEnd;
          const coincident = history.filter(
            (entry) =>
              !entry.unitMismatch &&
              entry.test_value === alarm.test_value &&
              Date.parse(entry.triggered_at) <= props.windowEnd &&
              visibleEnd(entry) >= props.windowStart &&
              Math.abs(visibleEnd(entry) - visibleEnd(alarm)) < 1000 &&
              props.series.some(
                (line) =>
                  line.bindingIds?.length === 1 &&
                  line.bindingIds[0] === entry.binding_id &&
                  line.yAxisId === measured.yAxisId &&
                  unitLabel(line.unit) === unitLabel(alarm.unit)
              )
          );
          return [
            {
              ...measured,
              key: `test-${alarm.anomaly_id}-${measured.key}`,
              measuredKey: measured.key,
              bindingIds: [],
              label:
                alarm.status === 'resolved'
                  ? t`TEST ${measured.label}: ${formatValue(alarm.test_value, 1, alarm.unit)} (resolved)`
                  : t`TEST ${measured.label}: ${formatValue(alarm.test_value, 1, alarm.unit)}`,
              color: scheme === 'dark' ? 'red.3' : 'red.9',
              synthetic: true,
              stepped: true,
              strokeWidth: 2.5,
              endLabel: coincident[0]?.anomaly_id === alarm.anomaly_id,
              endLabelText:
                coincident.length > 1
                  ? t`TEST ${coincident.length} channels: ${formatValue(alarm.test_value, 1, alarm.unit)}`
                  : undefined
            }
          ];
        })
      ].map((s) => ({
        ...s,
        color: legibleColor(s.color, theme, scheme)
      })),
    [props.series, theme, scheme, history, props.windowStart, props.windowEnd]
  );
  const visible = useMemo(
    () => visibleExerciseSeries(series, hidden),
    [series, hidden]
  );
  const referenceLines = useMemo(
    () =>
      (props.referenceLines ?? []).map((line) => ({
        ...line,
        color:
          line.severity === 'warning'
            ? 'var(--aimms-warning)'
            : getThemeColor(legibleColor(line.color, theme, scheme), theme)
      })),
    [props.referenceLines, theme, scheme]
  );
  const rows = useMemo(
    () =>
      exerciseRows(
        props.rows,
        visible
          .filter((line) => line.synthetic)
          .flatMap((line) => {
            const alarm = history.find((entry) =>
              line.key.startsWith(`test-${entry.anomaly_id}-`)
            );
            return alarm
              ? [
                  {
                    key: line.key,
                    value: alarm.test_value,
                    since: Date.parse(alarm.triggered_at),
                    ...(alarm.status === 'resolved'
                      ? { until: Date.parse(alarm.resolved_at ?? '') }
                      : {})
                  }
                ]
              : [];
          }),
        props.windowStart,
        props.windowEnd
      ),
    [props.rows, visible, history, props.windowStart, props.windowEnd]
  );
  const resolvedInWindow = history.some(
    (alarm) =>
      alarm.status === 'resolved' &&
      !alarm.unitMismatch &&
      Date.parse(alarm.triggered_at) <= props.windowEnd &&
      Date.parse(alarm.resolved_at ?? '') >= props.windowStart
  );

  const toggle = (key: string) =>
    setHidden((current) => toggleExerciseSeries(series, current, key));

  // The chips are the show/hide control as well as the key to the colours, so
  // the maximised view gets them too - the same node, and the same state,
  // because `hidden` lives here.
  const chips =
    (props.toggleable || history.length > 0) && series.length > 1 ? (
      <Group gap={4} wrap='wrap'>
        {series.map((line) => {
          const checked = visible.some((entry) => entry.key === line.key);
          const lastVisible =
            checked &&
            !visibleExerciseSeries(series, new Set([...hidden, line.key]))
              .length;
          const explanation = t`At least one line must remain visible.`;
          return (
            <Tooltip
              key={line.key}
              label={explanation}
              disabled={!lastVisible}
              events={{ hover: true, focus: true, touch: true }}
            >
              <Box>
                <Chip
                  size='xs'
                  variant='outline'
                  color={line.color}
                  checked={checked}
                  aria-description={lastVisible ? explanation : undefined}
                  onChange={() => toggle(line.key)}
                >
                  {line.label}
                </Chip>
              </Box>
            </Tooltip>
          );
        })}
      </Group>
    ) : null;

  // The card above already carries the title; only the modal, which replaces
  // that card, has to say what it is showing.
  const header = (
    <Group justify='space-between' align='flex-start' wrap='nowrap' gap='xs'>
      <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
        {chips}
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
    <Stack
      gap={6}
      data-trend-sync={props.syncId}
      className={operational.warning}
    >
      {header}
      <PerformanceTestNotice
        bindingIds={bindingIds}
        afterHistory={tests.some(
          (alarm) =>
            !alarm.unitMismatch &&
            Date.parse(alarm.triggered_at) > props.windowEnd
        )}
        historyAvailable={props.rows.some((row) =>
          visible.some(
            (line) =>
              typeof row[line.key] === 'number' &&
              Number.isFinite(row[line.key])
          )
        )}
      />
      {resolvedInWindow && (
        <MetaText size='xs'>{t`Resolved TEST trace is a synthetic exercise, not measured telemetry.`}</MetaText>
      )}
      {failureText &&
        props.rows.some((row) =>
          visible.some((line) => typeof row[line.key] === 'number')
        ) && <MetaText size='xs'>{failureText}</MetaText>}
      <ChartBody
        {...chartProps}
        rows={rows}
        measuredRows={props.rows}
        toggleable={props.toggleable || history.length > 0}
        series={visible}
        referenceLines={referenceLines}
      />
      <Modal
        opened={fullscreen}
        onClose={() => setFullscreen(false)}
        fullScreen
        title={props.title ?? cardTitle ?? t`Trend`}
      >
        <Stack gap={6}>
          <Stack gap={6} ref={fullscreenControls}>
            {chips}
            <PerformanceTestNotice
              bindingIds={bindingIds}
              afterHistory={tests.some(
                (alarm) =>
                  !alarm.unitMismatch &&
                  Date.parse(alarm.triggered_at) > props.windowEnd
              )}
              historyAvailable={props.rows.some((row) =>
                visible.some(
                  (line) =>
                    typeof row[line.key] === 'number' &&
                    Number.isFinite(row[line.key])
                )
              )}
            />
            {resolvedInWindow && (
              <MetaText size='xs'>{t`Resolved TEST trace is a synthetic exercise, not measured telemetry.`}</MetaText>
            )}
            {failureText &&
              props.rows.some((row) =>
                visible.some((line) => typeof row[line.key] === 'number')
              ) && <MetaText size='xs'>{failureText}</MetaText>}
          </Stack>
          <ChartBody
            {...chartProps}
            rows={rows}
            measuredRows={props.rows}
            toggleable={props.toggleable || history.length > 0}
            series={visible}
            referenceLines={referenceLines}
            height={Math.max(240, window.innerHeight - controlsHeight - 140)}
          />
        </Stack>
      </Modal>
    </Stack>
  );
}

function ChartBody({
  rows,
  measuredRows,
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
  const displayClock = useDisplayClock();
  const theme = useMantineTheme();
  const [includeLimits, setIncludeLimits] = useState(false);

  const plotted = useMemo(
    () =>
      rows.some((row) =>
        series.some(
          (s) => typeof row[s.key] === 'number' && Number.isFinite(row[s.key])
        )
      ),
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
        strokeWidth: s.strokeWidth,
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
  const lineAxis = (line: ReferenceLine) =>
    line.yAxisId === 'right' && withRight
      ? ('right' as const)
      : ('left' as const);
  const leftUnit = anyLeftAxis ? unit : rightUnit;

  // Axis widths follow the widest tick label they will print, so the ticks
  // themselves are computed here and handed to the axis: measured from the
  // data values instead, the width was short by the digits Recharts adds when
  // it rounds a range outwards ("-0.105" against a width fitting "-0.1"), and
  // the labels were clipped. The same walk collects each side's extremes,
  // which is what decides whether that side moved at all.
  const axisFor = (include: boolean) => {
    const side = (
      which: 'left' | 'right'
    ): {
      width: number;
      domain: [number | 'auto', number | 'auto'];
      ticks?: number[];
      allowDecimals?: boolean;
    } => {
      const own = series.filter((s) => {
        const onRight = s.yAxisId === 'right' && withRight;
        return (which === 'right') === onRight;
      });
      let min = Number.POSITIVE_INFINITY;
      let max = Number.NEGATIVE_INFINITY;
      for (const s of own) {
        for (const row of rows) {
          const v = row[s.key];
          if (v === null || v === undefined || !Number.isFinite(v)) continue;
          if (v < min) min = v;
          if (v > max) max = v;
        }
      }
      for (const line of referenceLines ?? []) {
        if (
          (include || line.synthetic) &&
          lineAxis(line) === which &&
          Number.isFinite(line.y)
        ) {
          min = Math.min(min, line.y);
          max = Math.max(max, line.y);
        }
      }
      const widthFor = (labels: string[]) =>
        14 + 7 * Math.min(Math.max(1, ...labels.map((l) => l.length)), 10);
      if (!Number.isFinite(min) || !Number.isFinite(max)) {
        return { width: widthFor(['0']), domain: axisDomain(min, max) };
      }
      if (min === max) {
        // The constant band says the value did not move - but the band is
        // derived from the value, so its edges are as unround as the reading
        // is: a voltage pinned at -592.5925903320312 gave Recharts the bounds
        // to tick between, and the axis printed "-586.666664428711". Tick it
        // at round numbers inside the band, as a moving axis is ticked.
        const [low, high] = axisDomain(min, max);
        if (typeof low !== 'number' || typeof high !== 'number') {
          return {
            width: widthFor([String(min)]),
            domain: axisDomain(min, max)
          };
        }
        const counted =
          own.length > 0 &&
          own.every(
            (line) =>
              (line.stepped || stepped) && (!line.unit || line.unit === '1')
          );
        const ticks = presentationTicks(
          niceTicks(low, high),
          min,
          own[0]?.unit ?? '',
          counted
        );
        if (ticks.length < 2) {
          return { width: widthFor([String(min)]), domain: [low, high] };
        }
        return {
          width: widthFor(ticks.map(String)),
          domain: [ticks[0], ticks[ticks.length - 1]] as [number, number],
          ticks,
          allowDecimals: !counted
        };
      }
      // A count or a state has no quarter: 0.25 of a running pump is not a
      // reading the dictionary has, so such an axis is ticked at its two whole
      // values. Whole readings, not merely a catalogue that rounds to whole
      // ones - a shaft speed printed as "745" still drifts between ticks.
      const counted =
        own.length > 0 &&
        own.every(
          (line) =>
            (line.stepped || stepped) && (!line.unit || line.unit === '1')
        );
      const ticks = presentationTicks(
        niceTicks(min, max),
        min,
        own[0]?.unit ?? '',
        counted
      );
      return {
        width: widthFor(ticks.map(String)),
        domain: [ticks[0], ticks[ticks.length - 1]] as [number, number],
        ticks,
        allowDecimals: !counted
      };
    };
    return { left: side('left'), right: side('right') };
  };
  const dataAxis = axisFor(false);
  const axis = includeLimits ? axisFor(true) : dataAxis;
  const hasOffScale = (referenceLines ?? []).some((line) => {
    const own = dataAxis[lineAxis(line)].domain;
    return line.y < Number(own[0]) || line.y > Number(own[1]);
  });

  // An axis with no unit is not self-explanatory when there are two of them:
  // a power factor drawn opposite megawatts gave two identical numeric
  // columns, neither saying which was which. Name the side by its series
  // instead, when it is the only one there.
  const titleFor = (which: 'left' | 'right') => {
    const own = series.filter(
      (s) => (s.yAxisId === 'right' && withRight ? 'right' : 'left') === which
    );
    const unitOf = which === 'right' ? rightUnit : leftUnit;
    if (unitOf) return unitLabel(unitOf);
    return own.length === 1 ? own[0].label : '';
  };
  const leftTitle = titleFor('left');
  const rightTitle = withRight ? titleFor('right') : '';

  // Ticks are sized to the plot, not to a fixed count: at 24 h each label
  // carries a date and six of them overprint each other on a narrow card.
  // Cards in a row are the same width, so they still share one list.
  const { ref: plotRef, width: plotWidth } = useElementSize<HTMLDivElement>();
  const ticks = useMemo(
    () =>
      timeTicks(
        windowStart,
        windowEnd,
        Math.max(
          2,
          Math.floor(
            (plotWidth || 600) / (windowSeconds > 12 * 3600 ? 116 : 68)
          )
        )
      ),
    [windowStart, windowEnd, plotWidth, windowSeconds]
  );

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
      <Paper withBorder p='md' h={height}>
        <Text size='sm' c={emptyText ? undefined : 'var(--mantine-color-text)'}>
          {emptyText ?? t`No readings in this window.`}
        </Text>
      </Paper>
    );
  }

  return (
    <Box ref={plotRef} className={operational.warning}>
      {/* Units sit upright above their axis; a one-letter unit rotated
          through ninety degrees ("V", "m") reads as a chevron or an E. */}
      {(leftTitle || rightTitle) && (
        <Group justify='space-between' gap='xs' px={4} mb={-4} wrap='nowrap'>
          <MetaText size='xs' truncate data-axis-title='left'>
            {leftTitle}
          </MetaText>
          {withRight && (
            <MetaText size='xs' truncate data-axis-title='right'>
              {rightTitle}
            </MetaText>
          )}
        </Group>
      )}
      <LineChart
        textColor='var(--mantine-color-text)'
        h={height}
        data={rows}
        dataKey='t'
        series={mantineSeries}
        curveType={stepped ? 'stepAfter' : 'linear'}
        withDots={false}
        connectNulls={false}
        strokeWidth={1.5}
        lineProps={(item) => {
          const original = byKey.get(item.name);
          const sourceRows = original?.synthetic
            ? rows
            : (measuredRows ?? rows);
          const last = sourceRows.findLastIndex(
            (row) =>
              typeof row[item.name] === 'number' &&
              Number.isFinite(row[item.name])
          );
          return {
            data: sourceRows,
            strokeWidth: original?.strokeWidth ?? 1.5,
            strokeDasharray: original?.synthetic ? '6 4' : undefined,
            dot: original?.synthetic
              ? (props: any) => {
                  const first = sourceRows.findIndex(
                    (row) =>
                      typeof row[item.name] === 'number' &&
                      Number.isFinite(row[item.name])
                  );
                  return (props.index === first || props.index === last) &&
                    Number.isFinite(Number(props.cx)) &&
                    Number.isFinite(Number(props.cy)) ? (
                    <circle
                      cx={props.cx}
                      cy={props.cy}
                      r={3}
                      fill={getThemeColor(original.color, theme)}
                    />
                  ) : (
                    <g />
                  );
                }
              : false,
            label: original?.endLabel
              ? (props: any) =>
                  props.index === last ? (
                    <text
                      x={Number(props.x) - 4}
                      y={Number(props.y) - 8}
                      textAnchor='end'
                      fill={
                        original.synthetic
                          ? getThemeColor(original.color, theme)
                          : 'var(--mantine-color-text)'
                      }
                      fontSize={11}
                    >
                      {original.endLabelText ?? original.label}
                    </text>
                  ) : (
                    <g />
                  )
              : undefined
          };
        }}
        // The toggle chips already name and colour every line; a legend as
        // well wraps under a narrow card and runs into whatever follows.
        withLegend={!toggleable && series.length <= 8}
        // The reserved band has to cover the 37px the legend renders. Its
        // order is the series order: Recharts sorts its legend by each item's
        // value, which is the series *key*, so the forebay card listed "Pumps
        // running" ahead of "Forebay level" - the reverse of the order its own
        // description names them in.
        legendProps={{
          verticalAlign: 'bottom',
          height: 40,
          itemSorter: null
        }}
        withRightYAxis={withRight}
        yAxisProps={{
          width: axis.left.width,
          allowDataOverflow: false,
          domain: axis.left.domain,
          ticks: axis.left.ticks,
          allowDecimals: axis.left.allowDecimals
        }}
        rightYAxisProps={{
          width: axis.right.width,
          allowDataOverflow: false,
          domain: axis.right.domain,
          ticks: axis.right.ticks,
          allowDecimals: axis.right.allowDecimals
        }}
        xAxisProps={{
          type: 'number',
          scale: 'time',
          domain: [windowStart, windowEnd],
          allowDataOverflow: true,
          ticks,
          interval: 0,
          tickFormatter: (value: number) =>
            formatTick(value, windowSeconds, displayClock)
        }}
        referenceLines={referenceLines?.map((line) => ({
          y: line.y,
          yAxisId: line.yAxisId === 'right' && withRight ? 'right' : 0,
          labelPosition: line.synthetic ? 'insideTopRight' : undefined,
          label: {
            value: line.label,
            fill: line.synthetic ? line.color : 'var(--mantine-color-text)'
          },
          strokeDasharray: line.synthetic ? '6 4' : undefined,
          color: line.color
        }))}
        lineChartProps={{
          syncId,
          syncMethod: 'value',
          // Both sides keep their margin: zeroed on the right, the last tick
          // label of a two-axis chart lost its final character.
          margin: { top: 8, right: 12, bottom: 0, left: 12 },
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
        {(['left', 'right'] as const).flatMap((side) =>
          thresholdBands(
            (referenceLines ?? []).filter((line) => lineAxis(line) === side),
            Number(axis[side].domain[0]),
            Number(axis[side].domain[1])
          ).map((band) => (
            <ReferenceArea
              key={`${side}-${band.severity}-${band.from}-${band.to}`}
              yAxisId={side === 'right' && withRight ? 'right' : 0}
              y1={band.from}
              y2={band.to}
              fill={
                band.severity === 'warning'
                  ? 'var(--aimms-warning)'
                  : 'var(--mantine-color-red-6)'
              }
              fillOpacity={0.08}
              strokeOpacity={0}
            />
          ))
        )}
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
      {(['left', 'right'] as const).map((side) => {
        const own = (referenceLines ?? []).filter(
          (line) => lineAxis(line) === side && !line.synthetic
        );
        return own.length ? (
          <ThresholdKey
            key={side}
            lines={own}
            unit={side === 'left' ? leftUnit : rightUnit}
            low={Number(axis[side].domain[0])}
            high={Number(axis[side].domain[1])}
            include={includeLimits}
            onInclude={setIncludeLimits}
            hasOffScale={own.some(
              (line) =>
                line.y < Number(dataAxis[side].domain[0]) ||
                line.y > Number(dataAxis[side].domain[1])
            )}
          />
        ) : null;
      })}
      <Group gap='sm'>
        {series
          .filter(
            (line) =>
              !rows.some(
                (row) =>
                  typeof row[line.key] === 'number' &&
                  Number.isFinite(row[line.key])
              )
          )
          .map((line) => (
            <MetaText
              size='xs'
              key={line.key}
            >{t`${line.label}: no samples in window`}</MetaText>
          ))}
      </Group>
      {/* The row's height is reserved from the start, and clears the legend
          above it: mounted only once a drag finished, it grew the card under
          the cursor and started inside the legend's glyphs. */}
      {onZoom && (
        <Group justify='flex-end' mt='xs' gap='xs' mih={22}>
          {zoomRange && (
            <>
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
            </>
          )}
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
    <Paper withBorder shadow='sm' p='xs' style={{ minWidth: 180 }}>
      <MetaText size='xs' mb={4}>
        {formatInstant(instant)}
      </MetaText>
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
                c={value === null ? 'var(--mantine-color-text)' : undefined}
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

/** WCAG's floor for a non-text graphic: a drawn line, a chip's outline. */
const MIN_CONTRAST = 3;

/**
 * The ladder that shipped, in the order it shipped: a middling shade first,
 * then far ones, so a two-line chart gets two shades that cannot be confused.
 * Which of them a hue can actually use is decided against the card below.
 */
const PREFERRED_SHADES: Record<'light' | 'dark', number[]> = {
  light: [6, 9, 7, 8],
  dark: [5, 3, 7, 4, 6, 2, 8]
};

function parseHex(color: string): [number, number, number] | null {
  const match = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim());
  if (!match) return null;
  const digits =
    match[1].length === 3 ? match[1].replace(/./g, (d) => d + d) : match[1];
  const channel = (i: number) => Number.parseInt(digits.slice(i, i + 2), 16);
  return [channel(0), channel(2), channel(4)];
}

function luminance([r, g, b]: [number, number, number]): number {
  const channel = (value: number) => {
    const v = value / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG contrast, or null when either colour is not a hex the theme gave us. */
function contrastRatio(a: string, b: string): number | null {
  const first = parseHex(a);
  const second = parseHex(b);
  if (!first || !second) return null;
  const one = luminance(first);
  const two = luminance(second);
  return (Math.max(one, two) + 0.05) / (Math.min(one, two) + 0.05);
}

/**
 * What the charts are drawn on: the card's own background, which is
 * `--mantine-color-body` - the theme's white on the light card, dark.7 on the
 * dark one (measured #fff and #242424). Read from the theme rather than
 * assumed, because a user's theme can set its own white.
 */
function cardBackground(theme: MantineTheme, scheme: 'light' | 'dark'): string {
  return scheme === 'dark' ? theme.colors.dark[7] : theme.white;
}

const legibleCache = new Map<string, number[]>();

/** The shades of one hue that clear 3:1 on that card, in palette order. */
function legibleShades(
  theme: MantineTheme,
  hue: string,
  scheme: 'light' | 'dark'
): number[] {
  const background = cardBackground(theme, scheme);
  const key = `${hue}:${background}`;
  const cached = legibleCache.get(key);
  if (cached) return cached;
  const shades = (theme.colors[hue] ?? [])
    .map((color, shade) => ({ shade, ratio: contrastRatio(color, background) }))
    .filter((s) => s.ratio !== null && s.ratio >= MIN_CONTRAST)
    .map((s) => s.shade);
  legibleCache.set(key, shades);
  return shades;
}

/**
 * Colours for a family of lines. One hue family, cycled through the shades of
 * that hue that are legible on the card, with a second hue interleaved once the
 * first runs out - so a dozen winding sensors read as one family rather than a
 * rainbow.
 *
 * The ladder cannot be fixed per scheme: shade 6 clears 3:1 on white for blue
 * but not for orange, teal or cyan, and shade 8 clears it on the dark card for
 * blue but not for indigo, grape or violet. So the ladder that shipped is
 * filtered against the theme's own palette and the card it is drawn on, then
 * topped back up - furthest from what is already chosen first - out of whatever
 * else that hue can spare. A hue with nothing legible at all keeps the shipped
 * ladder rather than collapsing to one colour.
 */
/**
 * The next hue round the wheel, for a family that outgrows its own shades.
 *
 * Eleven winding sensors against the three shades of orange a white card can
 * show would otherwise draw four of them the same colour. Continuing into the
 * neighbouring hue keeps the card reading as one warm family while letting
 * each sensor keep its own line.
 */
const RELATED_HUES: Record<string, string> = {
  indigo: 'blue',
  blue: 'cyan',
  cyan: 'violet',
  violet: 'grape',
  grape: 'indigo',
  teal: 'blue'
};

function hueSequence(hues: string[]): string[] {
  const order = [
    ...new Set(
      hues.map((hue) =>
        ['indigo', 'blue', 'cyan', 'violet', 'grape', 'teal'].includes(hue)
          ? hue
          : 'indigo'
      )
    )
  ];
  const seen = new Set(order);
  for (let i = 0; i < order.length; i++) {
    const next = RELATED_HUES[order[i]];
    if (next && !seen.has(next)) {
      seen.add(next);
      order.push(next);
    }
  }
  return order;
}

export function familyColors(
  count: number,
  hues: string[],
  theme: MantineTheme,
  scheme: 'light' | 'dark'
): string[] {
  const preferred = PREFERRED_SHADES[scheme];
  const ladderOf = (hue: string) => {
    const legible = legibleShades(theme, hue, scheme);
    if (legible.length === 0) return preferred;
    const ladder = preferred.filter((shade) => legible.includes(shade));
    const spare = legible.filter((shade) => !ladder.includes(shade));
    while (ladder.length < preferred.length && spare.length > 0) {
      let best = 0;
      let widest = -1;
      spare.forEach((shade, i) => {
        const gap = Math.min(...ladder.map((s) => Math.abs(s - shade)));
        if (gap > widest) {
          widest = gap;
          best = i;
        }
      });
      ladder.push(spare.splice(best, 1)[0]);
    }
    return ladder.length > 0 ? ladder : preferred;
  };
  const colors: string[] = [];
  const used = new Set<string>();
  for (const hue of hueSequence(hues.length > 0 ? hues : ['blue'])) {
    if (colors.length >= count) break;
    for (const shade of ladderOf(hue)) {
      const color = `${hue}.${shade}`;
      // Cycling the hue list used to restart its first ladder, so a
      // fourteenth bay was drawn in the same teal as the first. A colour is
      // only reused once the palette has nothing else to offer.
      if (used.has(color)) continue;
      used.add(color);
      colors.push(color);
      if (colors.length === count) break;
    }
  }
  // More members than the hues can distinguish: repeat in order, so the
  // repetition at least reads as a second pass rather than as a clash.
  for (let i = 0; colors.length < count && colors.length > 0; i++) {
    colors.push(colors[i % used.size]);
  }
  return colors;
}

/**
 * A fixed colour moved to the nearest shade of its own hue that clears 3:1 on
 * the card: shade 6 of a warm hue vanishes on white, shade 7 of gray vanishes
 * on the dark card. The hue is what the caller meant; the shade is what the
 * card allows. A hue with nothing that clears the floor - yellow reaches only
 * 2.99 on white - gets its most legible shade instead of its worst.
 */
export function legibleColor(
  color: string,
  theme: MantineTheme,
  scheme: 'light' | 'dark'
): string {
  const [hue, raw] = color.split('.');
  const shade = Number(raw);
  if (!theme.colors[hue] || !Number.isInteger(shade)) return color;
  const legible = legibleShades(theme, hue, scheme);
  const nearest = (shades: number[]) =>
    shades.reduce((a, b) =>
      Math.abs(a - shade) <= Math.abs(b - shade) ? a : b
    );
  if (legible.length === 0) {
    // Nothing clears it: the furthest from the card is the best on offer.
    const background = cardBackground(theme, scheme);
    let best = shade;
    let bestRatio = -1;
    theme.colors[hue].forEach((value, i) => {
      const ratio = contrastRatio(value, background) ?? -1;
      if (ratio > bestRatio) {
        bestRatio = ratio;
        best = i;
      }
    });
    return `${hue}.${best}`;
  }
  if (legible.includes(shade)) return color;
  // Away from the card - darker on white, lighter on the dark card - so the
  // colour keeps as much of its own chroma as it can.
  const away = legible.filter((s) =>
    scheme === 'dark' ? s < shade : s > shade
  );
  return `${hue}.${nearest(away.length > 0 ? away : legible)}`;
}

export default TrendChart;
