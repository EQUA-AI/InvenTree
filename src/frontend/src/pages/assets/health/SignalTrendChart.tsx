import { t } from '@lingui/core/macro';
import { AreaChart } from '@mantine/charts';
import {
  Alert,
  Badge,
  Box,
  Button,
  Group,
  Loader,
  Modal,
  Paper,
  Select,
  Stack,
  Text,
  Tooltip,
  useComputedColorScheme,
  useMantineTheme
} from '@mantine/core';
import { DateTimePicker } from '@mantine/dates';
import { useElementSize } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import {
  type ReactElement,
  type ReactNode,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState
} from 'react';
import { useSearchParams } from 'react-router-dom';
import { ReferenceArea } from 'recharts';
import { displayClockLabel, displayInstant } from '../displayClock';
import monitoring from '../monitoring.module.css';
import { ThresholdKey } from '../performance/ThresholdKey';
import { legibleColor } from '../performance/TrendChart';
import { limitLines } from '../performance/limitLines';
import { splitLimits, thresholdBands } from '../performance/thresholdBands';
import { useNow } from '../useNow';
import { MetaText } from './common';
import { shiftFacts } from './shiftFacts';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { MachineSignal, SignalTrend } from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';
import {
  type InvestigationSelection,
  InvestigationSlices
} from '../investigation/InvestigationSlices';
import { LinkedEvidence } from '../investigation/LinkedEvidence';
import {
  type InvestigationWindow,
  displayLinkedWindow
} from '../investigation/windows';
import { niceTicks, timeTicks, unitLabel } from '../performance/format';
import { HealthHistoryProvenance } from './HealthHistoryProvenance';
import {
  type PreparedTrend,
  type TrendRow,
  isolatedIndices,
  prepareTrend,
  trendResolutionSeconds
} from './trendSamples';

/**
 * Longest window the server will read, in seconds.
 *
 * Mirrors `MAX_TREND_WINDOW_SECONDS`. The server is the authority and rejects
 * anything longer with a 400; this copy exists only so the picker can say *why*
 * before spending a request. The bound is not arbitrary: sources here write a
 * sample every five seconds, so six hours is already ~4320 readings, and a
 * "last 7 days" option would be 120k - a range nothing could serve and no axis
 * could render.
 */
const MAX_WINDOW_SECONDS = 6 * 3600;

/** Sentinel for the range select; not a duration. */
/** What the source reports about the history it holds for this station. */
interface DataRange {
  available: boolean;
  from: string;
  to: string;
  hours_with_data?: number;
  hours_probed?: number;
  source_name?: string;
  reason?: string;
  display_shift_seconds?: number;
}

const CUSTOM = 'custom';

/** A range edge, short enough for a badge but unambiguous about the date. */
function formatRangeEdge(iso: string): string {
  return displayInstant(iso) ?? t`Time unavailable`;
}

/**
 * Preset look-back windows, in seconds.
 *
 * All within the server's maximum. Offering a range the server will refuse, or
 * silently shrink, would make the axis lie about which window was actually read.
 */
/**
 * Windows this source can actually serve.
 *
 * A snapshot is a whole-station document of ~55KB that must be fetched and
 * parsed to extract one tag, so a window's cost is its document count, not its
 * sample count. Measured against the live account: fifteen minutes returns in
 * about ten seconds, an hour times out, three hours took two minutes and six
 * hours four. None of the long ones draw a better line - a chart a few hundred
 * pixels wide cannot show three thousand points - so they are not offered.
 * A longer window is still reachable through a custom range, where the wait is
 * at least deliberate.
 */
const RANGES = [
  { value: '300', label: () => t`5 minutes` },
  { value: '600', label: () => t`10 minutes` },
  { value: '900', label: () => t`15 minutes` },
  { value: '1800', label: () => t`30 minutes` }
];

/** Format Mantine's picker value (`YYYY-MM-DD HH:mm:ss`) for the API. */
function toIso(value: string | null): string | null {
  if (!value) {
    return null;
  }
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.toDate().toISOString() : null;
}

/**
 * Validate a hand-picked window, returning the reason it cannot be read.
 *
 * Checked here rather than left to the 400 so the operator is told which rule
 * they broke while the picker is still open, instead of watching the chart
 * blank out.
 */
function customWindowError(
  from: string | null,
  to: string | null
): string | null {
  if (!from || !to) {
    return t`Pick both a start and an end for the window.`;
  }

  const start = dayjs(from);
  const end = dayjs(to);
  if (!start.isValid() || !end.isValid()) {
    return t`That is not a readable date and time.`;
  }
  if (!end.isAfter(start)) {
    return t`The end of the window must come after its start.`;
  }

  const seconds = end.diff(start, 'second');
  if (seconds > MAX_WINDOW_SECONDS) {
    const hours = (seconds / 3600).toFixed(1);
    return t`That window is ${hours} hours long. Health's source reader accepts at most ${MAX_WINDOW_SECONDS / 3600} hours per read.`;
  }

  return null;
}

/**
 * An axis tick. Ticks fall on whole minutes of the reader's clock, so seconds
 * would only ever print ":00"; full instants with seconds are in the tooltip
 * and the window caption instead.
 */
function formatTick(ms: number, withDate: boolean): string {
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) {
    return String(ms);
  }
  // Every window is now at most six hours, so clock times cannot repeat within
  // one axis and the date is redundant - except for a custom window picked on
  // an earlier day, where the time alone would not say *which* day.
  return dayjs(ms).format(withDate ? 'D MMM HH:mm' : 'HH:mm');
}

/** A full instant with seconds, for a tooltip or a caption. */
function formatInstant(ms: number): string {
  return displayInstant(ms) ?? t`Time unavailable`;
}

/** A clock shift as words: hours under two days, days beyond. */
function shiftWords(seconds: number): string {
  const hours = Math.abs(seconds) / 3600;
  if (hours < 48) {
    return t`${hours.toFixed(1)} hours`;
  }
  return t`${(hours / 24).toFixed(1)} days`;
}

/**
 * What a hovered row is: a reading at its time, a reading that cannot be
 * drawn and why, or a silence between two readings.
 */
function rowLabel(row: TrendRow | undefined, fallback: number): string {
  if (!row) {
    return formatInstant(fallback);
  }
  if (!row.measured) {
    return t`No readings from ${formatInstant(row.from)} to ${formatInstant(row.to)}`;
  }
  if (row.undrawn === 'not_numeric') {
    return t`${formatInstant(row.t)} - reading is not a number`;
  }
  if (row.undrawn === 'quality') {
    return t`${formatInstant(row.t)} - reported ${row.quality} quality, not drawn`;
  }
  return formatInstant(row.t);
}

/**
 * Explain an unavailable trend in the source's own terms.
 *
 * "No connector" and "the source broke" are different situations with different
 * fixes, and collapsing them into one message sends people to the wrong place.
 */
function unavailableMessage(trend: SignalTrend): string {
  switch (trend.reason) {
    case 'NO_CONNECTOR':
      return t`This source has no configured connector, so no history can be read.`;
    case 'HISTORY_UNSUPPORTED':
      return t`${trend.source_name} does not support historical windows. Check the separately reported current reading.`;
    case 'SOURCE_TIMEOUT':
      return t`${trend.source_name} did not answer within the read timeout. The failed read does not establish an empty record.`;
    case 'SOURCE_UNAVAILABLE':
      return t`${trend.source_name} could not be read for this window. The failed read does not establish an absence of data.`;
    default:
      return trend.detail ?? t`No trend data is available.`;
  }
}

/**
 * A time-range chart for one mapped signal.
 *
 * AIMMS stores no time series - `MachineSignalState` is a current-value cache -
 * so every window here is a *federated* read against the source historian,
 * bounded by the server before it is returned.
 *
 * Three things this deliberately refuses to do:
 *
 * * **Plot a line when the source cannot serve history.** `available: false`
 *   gets an explanation, not an empty axis that looks like a flat reading.
 * * **Hide truncation.** The server caps the sample count and drops the
 *   *newest* readings when a window overflows, because `read_window` fills from
 *   the oldest bucket forward and stops. A chart that concealed that would be
 *   asserting the plant did nothing during the missing period. The cap is
 *   derived from the six-hour window at the source's sampling rate, so a window
 *   within the limit should not overflow at all - if this warning appears, the
 *   source is writing faster than expected, which is worth knowing.
 * * **Interpolate gaps.** Samples are drawn where the historian actually had
 *   data. `connectNulls` is off, so a gap looks like a gap.
 */
export function SignalTrendChart({
  machineId,
  signals,
  bindingId,
  onBindingChange,
  onInspect,
  evidence,
  inspectId: suppliedInspectId
}: Readonly<{
  machineId: number;
  inspectId?: string;
  signals: MachineSignal[];
  bindingId: string | null;
  onBindingChange: (value: string | null) => void;
  onInspect: (trigger: HTMLElement) => void;
  evidence?: (
    prepared: PreparedTrend,
    available: boolean,
    provenance: ReactNode
  ) => ReactNode;
}>) {
  const api = useApi();
  const now = useNow();
  const localInspectId = useId();
  const inspectId = suppliedInspectId ?? localInspectId;
  const [params, setParams] = useSearchParams();
  const [investigationOpen, setInvestigationOpen] = useState(false);
  const [caseSelection, setCaseSelection] = useState<{
    key: string;
    selection: InvestigationSelection;
  } | null>(null);
  const clearLinked = () => {
    setSelectedSlice(null);
    setParams(
      (current) => {
        const next = new URLSearchParams(current);
        for (const key of ['history_from', 'history_to', 'history_clock'])
          next.delete(key);
        return next;
      },
      { replace: true }
    );
  };

  // Only signals that can be charted at all. A status code has no numeric axis.
  const selectable = useMemo(
    () => signals.filter((signal) => signal.binding_id != null),
    [signals]
  );

  const [expanded, setExpanded] = useState(false);
  const expandTrigger = useRef<HTMLButtonElement>(null);
  // Ten minutes, not an hour. Each snapshot is a whole-station document of
  // ~55KB that the source must fetch and parse, so an hour is ~720 of them -
  // tens of megabytes for a line a few hundred pixels wide. Ten minutes is
  // ~120 samples, which is more than the chart can distinguish anyway.
  // What history the source actually holds. Recorded server-side, because the
  // edges can only be found by probing partitions - far too slow for a page.
  const rangeQuery = useQuery<DataRange>({
    queryKey: ['machine-health-data-range', machineId],
    staleTime: params.get('history_clock') === 'recorded' ? 0 : 60 * 60 * 1000,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_data_range, machineId)
      );
      return response.data;
    }
  });
  const recordedLinkKey =
    params.get('history_clock') === 'recorded'
      ? `${params.get('history_from')}:${params.get('history_to')}`
      : null;
  const { refetch: refreshRange } = rangeQuery;
  useEffect(() => {
    if (recordedLinkKey) void refreshRange();
  }, [recordedLinkKey, refreshRange]);
  const dataRange = rangeQuery.data;
  const linked = displayLinkedWindow(
    params,
    rangeQuery.isFetching || rangeQuery.isError
      ? undefined
      : dataRange?.display_shift_seconds
  );
  // Only a recorded-clock link depends on the offset: a fresh offset converts
  // it again. Manual and display-clock reads keep their slice when the source
  // extent arrives or refreshes.
  const linkKey = `${params.get('history_from')}:${params.get('history_to')}:${params.get('history_clock')}:${params.get('history_clock') === 'recorded' ? dataRange?.display_shift_seconds : ''}`;
  const [selectedSlice, setSelectedSlice] = useState<{
    linkKey: string;
    window: InvestigationWindow;
  } | null>(null);
  const slice =
    selectedSlice?.linkKey === linkKey ? selectedSlice.window : null;
  const needsSlice =
    !slice &&
    !!linked.window &&
    linked.window.to - linked.window.from > MAX_WINDOW_SECONDS * 1000;
  const blockedLink = linked.present && (!linked.window || needsSlice);
  const fixedWindow = slice ?? linked.window;
  const fixedRange =
    fixedWindow && !needsSlice
      ? {
          from: new Date(fixedWindow.from).toISOString(),
          to: new Date(fixedWindow.to).toISOString()
        }
      : null;

  // Ten minutes, not an hour. Each snapshot is a whole-station document of
  // ~55KB that the source must fetch and parse, so an hour is ~720 of them -
  // tens of megabytes for a line a few hundred pixels wide. Ten minutes is
  // ~120 samples, which is more than the chart can distinguish anyway.
  const [rangeSeconds, setRangeSeconds] = useState<string>('600');
  const [customFrom, setCustomFrom] = useState<string | null>(null);
  const [customTo, setCustomTo] = useState<string | null>(null);

  const isCustom = rangeSeconds === CUSTOM;

  const rangeError = useMemo(
    () => (isCustom ? customWindowError(customFrom, customTo) : null),
    [isCustom, customFrom, customTo]
  );

  // The explicit window, when one was picked and is readable.
  const customRange = useMemo(() => {
    if (!isCustom || rangeError) {
      return null;
    }
    const from = toIso(customFrom);
    const to = toIso(customTo);
    return from && to ? { from, to } : null;
  }, [isCustom, rangeError, customFrom, customTo]);

  const investigating = linked.present || !!slice;
  const requestedRange = investigating ? fixedRange : customRange;
  const sampleBudget = 4320;

  const windowSeconds = useMemo(() => {
    if (!isCustom) {
      return Number(rangeSeconds);
    }
    return customRange
      ? dayjs(customRange.to).diff(customRange.from, 'second')
      : 0;
  }, [isCustom, rangeSeconds, customRange]);

  // A window that starts on an earlier day needs its date on the axis.
  const withDate = useMemo(
    () =>
      !!requestedRange && !dayjs(requestedRange.from).isSame(dayjs(), 'day'),
    [requestedRange?.from]
  );

  const selectedIsMapped = selectable.some(
    (signal) => String(signal.binding_id) === bindingId
  );
  const trendQuery = useQuery<SignalTrend>({
    queryKey: [
      'machine-health-trend-chart',
      machineId,
      bindingId,
      rangeSeconds,
      requestedRange?.from ?? null,
      requestedRange?.to ?? null,
      sampleBudget
    ],
    // A custom window is only requested once it is complete and legal; an
    // incomplete one is a half-filled form, not a failed read.
    enabled:
      selectedIsMapped &&
      !blockedLink &&
      (investigating || !isCustom || customRange != null),
    retry: false,
    staleTime: 60 * 1000,
    queryFn: async ({ signal }) => {
      // Relative to now, and resolved at fetch time rather than at render so a
      // chart left open does not keep asking for the window it was mounted in.
      //
      // "Now" is correct even though the readings are a year old: the server
      // presents this source's history in the clock the dashboard shows, so the
      // newest reading is always at the present edge. Waiting on the data-range
      // request to anchor this would delay the first plot for an answer it
      // already has; that request only bounds the custom pickers.
      const end = Date.now();
      const window = requestedRange ?? {
        from: new Date(end - windowSeconds * 1000).toISOString(),
        to: new Date(end).toISOString()
      };
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_trend, machineId),
        {
          params: {
            binding: bindingId,
            from: window.from,
            to: window.to,
            max_samples: sampleBudget
          },
          signal,
          // This is the widest read in the panel - the range picker goes to the
          // service limit of six hours - and the global default is 5s. The
          // server bounds the window; the client just has to wait for it.
          timeout: 60 * 1000
        }
      );
      return response.data;
    }
  });

  const trend =
    selectedIsMapped && !blockedLink && !trendQuery.isError
      ? trendQuery.data
      : undefined;

  // Every reading at its own time, through the reading the row graphs share:
  // undrawable readings stay as nulls where they happened and silences longer
  // than the source's cadence become breaks, so nothing is joined across
  // missing time (R6 A05.3, A05.9). The old preparation dropped undrawable
  // samples and put the rest on a category axis, so a two-minute silence drew
  // as one step and its edges were joined.
  const prepared = useMemo(() => prepareTrend(trend), [trend]);
  const shift = shiftFacts(trend?.display_shift_seconds);

  if (selectable.length === 0) {
    return (
      <Paper withBorder p='md'>
        <MetaText>{t`No mapped signals are available to chart.`}</MetaText>
      </Paper>
    );
  }

  const caseKey = `${linked.window?.from ?? 'manual'}:${linked.window?.to ?? 'manual'}`;
  const provenance = (
    <HealthHistoryProvenance
      trend={trend}
      loading={!blockedLink && trendQuery.isFetching}
      failed={trendQuery.isError && !blockedLink}
      fixed={!!requestedRange}
      blocked={blockedLink}
      window={requestedRange}
      targetSource={
        selectable.find((item) => String(item.binding_id) === bindingId)
          ?.source_name
      }
    />
  );
  const historyStatus = (
    trendQuery.error as { response?: { status?: number } } | null
  )?.response?.status;
  const historyDenied = historyStatus === 403 || historyStatus === 401;
  const content = (
    <Paper withBorder p='xs'>
      <Stack gap={6}>
        {params.has('occurrence') && <LinkedEvidence machineId={machineId} />}
        {provenance}
        {blockedLink && (
          <Alert
            color='yellow'
            title={
              linked.clockUnknown
                ? t`Recorded clock unknown`
                : needsSlice
                  ? t`Choose a six-hour slice`
                  : t`Invalid investigation window`
            }
          >
            {linked.clockUnknown
              ? t`The recorded-to-display clock offset is unavailable or still loading. No selected history is read until the clock is known.`
              : needsSlice
                ? t`This linked span exceeds Health's 6 h read limit. Choose and explicitly read a slice below; the span is not silently shortened.`
                : t`Linked bounds must be valid, increasing and at most 24 h. No history was requested from this link.`}
            {linked.clockUnknown && (
              <Button
                className={monitoring.link}
                size='xs'
                variant='subtle'
                onClick={() => rangeQuery.refetch()}
              >{t`Retry source extent`}</Button>
            )}
            <Button
              className={monitoring.link}
              size='xs'
              variant='subtle'
              onClick={clearLinked}
            >{t`Use current range controls`}</Button>
          </Alert>
        )}

        {(investigationOpen || needsSlice) && (
          <InvestigationSlices
            key={caseKey}
            selection={
              caseSelection?.key === caseKey
                ? caseSelection.selection
                : undefined
            }
            onSelectionChange={(selection) =>
              setCaseSelection({ key: caseKey, selection })
            }
            initial={
              linked.window ?? {
                from: requestedRange
                  ? Date.parse(requestedRange.from)
                  : now - 600000,
                to: requestedRange ? Date.parse(requestedRange.to) : now
              }
            }
            maxWindowMs={MAX_WINDOW_SECONDS * 1000}
            onRead={(window) => {
              setInvestigationOpen(true);
              // Keep the case span and its form identity separate from the read
              // slice, including when no investigation arrived in the URL.
              setSelectedSlice({ linkKey, window });
            }}
          />
        )}
        <Group gap='sm' align='flex-end' wrap='wrap'>
          <Button
            className={monitoring.link}
            variant='subtle'
            size='xs'
            w='fit-content'
            aria-expanded={investigationOpen || needsSlice}
            onClick={() => setInvestigationOpen((value) => !value)}
          >{t`Case span investigation`}</Button>
          {!expanded && (
            <Button
              ref={expandTrigger}
              variant='default'
              onClick={() => setExpanded(true)}
            >{t`Expand chart`}</Button>
          )}
          <Button
            className={monitoring.link}
            variant='subtle'
            id={inspectId}
            data-health-inspect='true'
            onClick={(event) => onInspect(event.currentTarget)}
          >{t`Inspect selected signal`}</Button>
          <Select
            classNames={{ input: monitoring.input }}
            label={t`Parameter`}
            allowDeselect={false}
            searchable
            value={bindingId}
            onChange={onBindingChange}
            data={selectable.map((signal) => ({
              value: String(signal.binding_id),
              label: signal.unit
                ? `${signal.display_name} (${unitLabel(signal.unit)})`
                : signal.display_name
            }))}
            // 320 px from the xs breakpoint up, as before; on a phone the
            // card is narrower than that, so the field takes its width.
            w={{ base: '100%', xs: 'auto' }}
            miw={{ base: 0, xs: 320 }}
          />
          <Select
            classNames={{ input: monitoring.input }}
            label={t`Range`}
            value={investigating ? 'investigation' : rangeSeconds}
            onChange={(value) => {
              if (value === 'investigation') return;
              clearLinked();
              setRangeSeconds(value ?? '600');
            }}
            data={[
              ...(investigating
                ? [
                    {
                      value: 'investigation',
                      // A slice read from a span typed here was not linked.
                      label: linked.present
                        ? t`Linked investigation window`
                        : t`Selected case slice`
                    }
                  ]
                : []),
              ...RANGES.map((range) => ({
                value: range.value,
                label: range.label()
              })),
              { value: CUSTOM, label: t`Custom range…` }
            ]}
            allowDeselect={false}
            style={{ minWidth: 170 }}
          />
          {isCustom && !investigating && (
            <>
              <DateTimePicker
                classNames={{ input: monitoring.input }}
                label={t`From`}
                value={customFrom}
                onChange={setCustomFrom}
                valueFormat='YYYY-MM-DD HH:mm:ss'
                withSeconds
                clearable
                minDate={
                  dataRange?.available ? new Date(dataRange.from) : undefined
                }
                maxDate={
                  dataRange?.available ? new Date(dataRange.to) : new Date()
                }
                placeholder={t`Start of window`}
                style={{ minWidth: 210 }}
              />
              <DateTimePicker
                classNames={{ input: monitoring.input }}
                label={t`To`}
                value={customTo}
                onChange={setCustomTo}
                valueFormat='YYYY-MM-DD HH:mm:ss'
                withSeconds
                clearable
                minDate={
                  customFrom ??
                  (dataRange?.available ? new Date(dataRange.from) : undefined)
                }
                maxDate={
                  dataRange?.available ? new Date(dataRange.to) : new Date()
                }
                placeholder={t`End of window`}
                style={{ minWidth: 210 }}
              />
            </>
          )}
        </Group>
        <Group gap={6}>
          {' '}
          {trend?.available && (
            <Badge
              variant='outline'
              color='gray'
              tt='none'
              c='var(--mantine-color-text)'
            >
              {t`${prepared.measured} samples`}
            </Badge>
          )}
          {dataRange?.available && (
            <Tooltip
              events={{ hover: true, focus: true, touch: false }}
              label={t`Reported source extent: ${formatRangeEdge(dataRange.from)} to ${formatRangeEdge(dataRange.to)}. Extent metadata does not establish samples or completeness in any selected window.`}
            >
              <Badge
                tabIndex={0}
                variant='outline'
                color='gray'
                tt='none'
                c='var(--mantine-color-text)'
              >
                {t`Data to ${formatRangeEdge(dataRange.to)}`}
              </Badge>
            </Tooltip>
          )}
        </Group>

        {!selectedIsMapped && (
          <Alert
            color='gray'
            title={t`Selected signal is no longer mapped here`}
          >
            {t`Its selection is retained. Choose an available parameter to read current history; no other signal is substituted.`}
          </Alert>
        )}

        {rangeError && !investigating && (
          <Alert color='gray' title={t`Choose a window to read`}>
            {rangeError}
          </Alert>
        )}

        {!blockedLink && trendQuery.isFetching && (
          <Loader size='sm' aria-label={t`Reading signal history`} />
        )}
        {!blockedLink && trendQuery.isFetching && trend && (
          <Text size='xs'>{t`Updating the selected source window; the previous read remains visible.`}</Text>
        )}

        {trendQuery.isError && !blockedLink && (
          <Alert
            color='red'
            classNames={{ label: monitoring.error }}
            title={
              historyDenied
                ? t`History access denied`
                : t`Could not load the trend`
            }
          >
            {historyDenied
              ? t`This source window could not be read in your current scope. No history or empty record is inferred.`
              : t`The request for this window failed. No data is shown rather than a partial line.`}
            <Button
              className={monitoring.link}
              size='xs'
              variant='subtle'
              onClick={() => trendQuery.refetch()}
            >{t`Retry history`}</Button>
          </Alert>
        )}

        {trend && !trend.available && (
          <Alert
            color='gray'
            title={
              trend.reason === 'SOURCE_UNAVAILABLE' ||
              trend.reason === 'SOURCE_TIMEOUT'
                ? t`Could not read the source`
                : t`No history for this signal`
            }
          >
            {unavailableMessage(trend)}
          </Alert>
        )}

        {trend?.available && trend.truncated && (
          <Alert color='yellow' title={t`This window was truncated`}>
            {t`The source returned more than the ${trend.limits.max_samples} sample limit, so the most recent part of this window is not shown. Choose a shorter range to see it.`}
          </Alert>
        )}

        {trend?.available &&
          prepared.measured === 0 &&
          prepared.undated === 0 && (
            <Alert color='gray' title={t`No samples in this window`}>
              {t`${trend.source_name} served this window but had no data in it. That is different from the source being unable to answer, and from a recorded zero.`}
            </Alert>
          )}

        {trend?.available && prepared.drawn > 1 && (
          <TrendPlot
            trend={trend}
            prepared={prepared}
            withDate={withDate}
            limits={
              signals.find((signal) => String(signal.binding_id) === bindingId)
                ?.limits
            }
          />
        )}

        {trend?.available &&
          prepared.drawn === 0 &&
          (prepared.measured > 0 || prepared.undated > 0) && (
            <Alert color='gray' title={t`No drawable readings`}>
              {t`Readings arrived, but none have both a readable time and a numeric value reported good quality. This is different from an empty or sparse record.`}
            </Alert>
          )}
        {trend?.available && prepared.drawn === 1 && (
          <Alert color='gray' title={t`Only one drawable reading`}>
            {t`1 of ${prepared.measured} readings can be drawn. A single point is not a trend.`}
          </Alert>
        )}

        {trend?.available && (
          <Stack gap={2}>
            <MetaText size='xs'>
              {t`Selected window: ${formatInstant(prepared.start)} to ${formatInstant(prepared.end)}.`}{' '}
              {expanded && (
                <>{t`Times: your clock (${displayClockLabel()})`}. </>
              )}
              {t`Read from ${trend.source_name}. AIMMS does not store this history.`}
            </MetaText>
            {trend.display_shifted ? (
              <MetaText size='xs'>
                {shift.seconds === null
                  ? t`Source history is display shifted; the amount is unavailable.`
                  : t`Times are shown on the dashboard clock, ${shiftWords(shift.seconds)} ${shift.direction === 'earlier' ? t`earlier than the source recorded them` : t`later than the source recorded them`}.`}
              </MetaText>
            ) : null}
          </Stack>
        )}
        <MetaText
          maw='70ch'
          size='xs'
        >{t`Current values and recent row trends differ from selected history; retained snapshots keep their capture-time origin. No resampling resolution or retention completeness is supplied. Fast transients may be absent; gaps are not filled.`}</MetaText>
        {trend?.available && <RecordGaps trend={trend} prepared={prepared} />}
        {evidence?.(prepared, !!trend?.available, provenance)}
      </Stack>
    </Paper>
  );
  return (
    <>
      {!expanded && content}
      <Modal
        opened={expanded}
        onClose={() => setExpanded(false)}
        title={t`Expanded signal trend`}
        closeButtonProps={{ 'aria-label': t`Close expanded signal trend` }}
        size='calc(100vw - 32px)'
        fullScreen={false}
        returnFocus={false}
        onExitTransitionEnd={() => expandTrigger.current?.focus()}
      >
        {expanded && content}
      </Modal>
    </>
  );
}

/**
 * Where the record has holes, said once above the chart: silences longer than
 * the source's cadence, readings that cannot be drawn, and readings with no
 * readable time. Each is a break at its own time, never a filled value.
 */
function RecordGaps({
  trend,
  prepared
}: Readonly<{ trend: SignalTrend; prepared: PreparedTrend }>) {
  const undrawn = prepared.notNumeric + prepared.notGood;
  const cadence = trendResolutionSeconds(trend);
  const lines: string[] = [];
  if (prepared.silences > 0 && cadence !== null) {
    lines.push(
      trend.cadence_seconds != null && trend.cadence_seconds > 0
        ? t`Gaps longer than ${prepared.gapMs! / 1000} seconds: ${prepared.silences}. Based on the measured ${cadence}-second interval.`
        : t`Gaps longer than ${prepared.gapMs! / 1000} seconds: ${prepared.silences}. Based on the expected ${cadence}-second interval (server default).`
    );
  }
  if (prepared.notNumeric > 0) {
    lines.push(
      t`Readings that are not numbers: ${prepared.notNumeric} of ${prepared.measured}.`
    );
  }
  if (prepared.notGood > 0) {
    lines.push(
      t`Readings not reported good quality: ${prepared.notGood} of ${prepared.measured}.`
    );
  }
  if (prepared.undated > 0) {
    lines.push(
      t`Readings with no readable time, which cannot be placed on the axis: ${prepared.undated}.`
    );
  }
  if (lines.length === 0 && (cadence !== null || prepared.measured < 2)) {
    return null;
  }
  return (
    <Alert color='gray' title={t`Gaps in this record`}>
      <Stack gap={2}>
        {lines.map((line) => (
          <Text key={line} size='sm'>
            {line}
          </Text>
        ))}
        {prepared.drawn > 1 && (prepared.silences > 0 || undrawn > 0) && (
          <Text size='sm'>
            {t`Each is drawn as a break at its own time. Nothing is filled in, coerced to a number or joined across.`}
          </Text>
        )}
        {cadence === null && (
          <Text size='sm'>
            {t`This source did not state its sampling interval, so silences between readings are not marked; only unreadable readings break the line.`}
          </Text>
        )}
      </Stack>
    </Alert>
  );
}

/**
 * The native full blue AreaChart, on a time axis.
 *
 * Same series colour, fill, linear interpolation, height and dot rule as
 * before; what changed is the axis under it. Readings sit at their own
 * times across the selected window, nulls and silence breaks keep the line
 * open (`connectNulls` off), and a reading alone between two breaks still
 * gets a dot so it is not lost.
 */
export function TrendPlot({
  trend,
  prepared,
  withDate,
  height = 320,
  limits
}: Readonly<{
  trend: SignalTrend;
  prepared: PreparedTrend;
  withDate: boolean;
  height?: number;
  limits?: MachineSignal['limits'];
}>) {
  const unit = trend.unit ?? '';
  const theme = useMantineTheme();
  const scheme = useComputedColorScheme('light');
  const [includeLimits, setIncludeLimits] = useState(false);
  const lines = useMemo(() => limitLines(limits ?? {}, unit), [limits, unit]);
  const values = prepared.rows.flatMap((row) =>
    typeof row.value === 'number' && Number.isFinite(row.value)
      ? [row.value]
      : []
  );
  const extent = values.length
    ? [Math.min(...values), Math.max(...values)]
    : [0, 1];
  const dataTicks = niceTicks(
    extent[0] === extent[1] ? extent[0] - 1 : extent[0],
    extent[0] === extent[1] ? extent[1] + 1 : extent[1]
  );
  const limitsOffScale =
    splitLimits(lines, dataTicks[0], dataTicks.at(-1)!).offScale.length > 0;
  const scaleValues = includeLimits
    ? [...extent, ...lines.map((line) => line.y)]
    : extent;
  const scaleTicks = includeLimits
    ? niceTicks(Math.min(...scaleValues), Math.max(...scaleValues))
    : dataTicks;
  const domain: [number, number] = [scaleTicks[0], scaleTicks.at(-1)!];
  // Ticks sized to the plot, as on the Performance charts: a clock label
  // needs about 90 px, so a phone gets a five-minute grid rather than only
  // the two ends.
  const { ref: plotRef, width: plotWidth } = useElementSize<HTMLDivElement>();
  const ticks = useMemo(
    () =>
      timeTicks(
        prepared.start,
        prepared.end,
        Math.max(2, Math.floor((plotWidth || 600) / (withDate ? 130 : 90)))
      ),
    [prepared.start, prepared.end, plotWidth, withDate]
  );
  const isolated = useMemo(
    () => isolatedIndices(prepared.rows),
    [prepared.rows]
  );
  const withDots = prepared.drawn <= 60;

  // Dots are drawn for every reading on a sparse chart; on a dense one only
  // a reading with no drawn neighbour needs one to stay visible.
  const isolatedDot = (props: {
    cx?: number;
    cy?: number;
    index?: number;
  }): ReactElement => {
    const { cx, cy, index } = props;
    if (
      index === undefined ||
      !isolated.has(index) ||
      cx === undefined ||
      cy === undefined
    ) {
      return <g key={`dot-${index}`} />;
    }
    return (
      <circle
        key={`dot-${index}`}
        cx={cx}
        cy={cy}
        r={2.5}
        fill='var(--mantine-color-blue-6)'
      />
    );
  };

  return (
    <Box ref={plotRef} data-testid='health-trend-plot'>
      <AreaChart
        styles={{
          axisLabel: {
            fill: 'var(--mantine-color-text)'
          }
        }}
        textColor='var(--mantine-color-text)'
        h={height}
        data={prepared.rows as unknown as Record<string, unknown>[]}
        dataKey='t'
        series={[{ name: 'value', label: trend.display_name, color: 'blue.6' }]}
        curveType='linear'
        connectNulls={false}
        withDots={withDots}
        areaProps={
          !withDots && isolated.size > 0 ? { dot: isolatedDot } : undefined
        }
        yAxisLabel={unitLabel(unit)}
        yAxisProps={{
          domain,
          ticks: scaleTicks,
          tickFormatter: (value: number) => String(value),
          allowDataOverflow: true
        }}
        referenceLines={lines.map((line) => ({
          y: line.y,
          label: { value: line.label, fill: 'var(--mantine-color-text)' },
          color: legibleColor(line.color, theme, scheme)
        }))}
        xAxisProps={{
          type: 'number',
          scale: 'time',
          domain: [prepared.start, prepared.end],
          allowDataOverflow: true,
          ticks,
          // The ticks are already sized to the plot; this only stops a label
          // printing over the nudged final one.
          minTickGap: 12,
          tickFormatter: (value: number) => formatTick(value, withDate)
        }}
        tooltipProps={{
          labelFormatter: (label: unknown, payload: readonly unknown[]) => {
            const row = (payload?.[0] as { payload?: TrendRow } | undefined)
              ?.payload;
            return rowLabel(row, Number(label));
          }
        }}
        valueFormatter={(value) =>
          value === null || value === undefined
            ? '—'
            : unit
              ? `${value} ${unitLabel(unit)}`
              : String(value)
        }
      >
        {thresholdBands(lines, domain[0], domain[1]).map((band) => (
          <ReferenceArea
            key={`${band.severity}-${band.from}-${band.to}`}
            y1={band.from}
            y2={band.to}
            fill={`var(--mantine-color-${band.severity === 'critical' ? 'red' : 'yellow'}-6)`}
            fillOpacity={0.08}
            strokeOpacity={0}
          />
        ))}
      </AreaChart>
      <ThresholdKey
        unit={unit}
        lines={lines}
        low={domain[0]}
        high={domain[1]}
        include={includeLimits}
        onInclude={setIncludeLimits}
        hasOffScale={limitsOffScale}
      />
    </Box>
  );
}

export default SignalTrendChart;
