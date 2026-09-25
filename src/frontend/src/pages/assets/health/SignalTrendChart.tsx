import { t } from '@lingui/core/macro';
import { AreaChart } from '@mantine/charts';
import {
  Alert,
  Badge,
  Group,
  Loader,
  Paper,
  Select,
  Stack,
  Text
} from '@mantine/core';
import { DateTimePicker } from '@mantine/dates';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { MachineSignal, SignalTrend } from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';

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
const CUSTOM = 'custom';

/**
 * Preset look-back windows, in seconds.
 *
 * All within the server's maximum. Offering a range the server will refuse, or
 * silently shrink, would make the axis lie about which window was actually read.
 */
const RANGES = [
  { value: '900', label: () => t`Last 15 minutes` },
  { value: '3600', label: () => t`Last hour` },
  { value: '10800', label: () => t`Last 3 hours` },
  { value: `${MAX_WINDOW_SECONDS}`, label: () => t`Last 6 hours` }
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
    return t`That window is ${hours} hours long. These signals are sampled every few seconds, so a window may not exceed ${MAX_WINDOW_SECONDS / 3600} hours.`;
  }

  return null;
}

/** Values the historian can return that cannot be plotted on a numeric axis. */
function numericValue(value: unknown): number | null {
  if (value === null || value === undefined || typeof value === 'boolean') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatTimestamp(
  iso: string,
  windowSeconds: number,
  withDate: boolean
): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  // Every window is now at most six hours, so clock times cannot repeat within
  // one axis and the date is redundant - except for a custom window picked on
  // an earlier day, where the time alone would not say *which* day.
  return date.toLocaleString(undefined, {
    ...(withDate ? { month: 'short', day: 'numeric' } : {}),
    hour: '2-digit',
    minute: '2-digit',
    // Seconds only matter when the window is short enough for them to be
    // distinguishable; over six hours they are noise.
    ...(windowSeconds <= 3600 ? { second: '2-digit' } : {})
  });
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
      return t`${trend.source_name} cannot serve historical windows. Only the current value is available.`;
    case 'SOURCE_UNAVAILABLE':
      return t`${trend.source_name} could not be reached for this window. This is an outage, not an absence of data.`;
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
  signals
}: Readonly<{ machineId: number; signals: MachineSignal[] }>) {
  const api = useApi();

  // Only signals that can be charted at all. A status code has no numeric axis.
  const selectable = useMemo(
    () => signals.filter((signal) => signal.binding_id != null),
    [signals]
  );

  const [bindingId, setBindingId] = useState<string | null>(
    selectable.length > 0 ? String(selectable[0].binding_id) : null
  );
  const [rangeSeconds, setRangeSeconds] = useState<string>('3600');
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
    () => !!customRange && !dayjs(customRange.from).isSame(dayjs(), 'day'),
    [customRange]
  );

  const trendQuery = useQuery<SignalTrend>({
    queryKey: [
      'machine-health-trend-chart',
      machineId,
      bindingId,
      rangeSeconds,
      customRange?.from ?? null,
      customRange?.to ?? null
    ],
    // A custom window is only requested once it is complete and legal; an
    // incomplete one is a half-filled form, not a failed read.
    enabled: bindingId != null && (!isCustom || customRange != null),
    staleTime: 60 * 1000,
    queryFn: async () => {
      // A preset is relative to *now*, so it is resolved at fetch time rather
      // than at render: a chart left open overnight should not keep asking for
      // the hour in which it was mounted.
      const window = customRange ?? {
        from: new Date(Date.now() - windowSeconds * 1000).toISOString(),
        to: new Date().toISOString()
      };
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_trend, machineId),
        {
          params: {
            binding: bindingId,
            from: window.from,
            to: window.to
          },
          // This is the widest read in the panel - the range picker goes to the
          // service limit of six hours - and the global default is 5s. The
          // server bounds the window; the client just has to wait for it.
          timeout: 60 * 1000
        }
      );
      return response.data;
    }
  });

  const trend = trendQuery.data;

  const { points, skipped } = useMemo(() => {
    const samples = trend?.samples ?? [];
    const plotted: { label: string; value: number }[] = [];
    let ignored = 0;

    // Samples arrive oldest-first, which is already left-to-right.
    for (const sample of samples) {
      const value = numericValue(sample.value);
      if (value === null) {
        ignored += 1;
        continue;
      }
      plotted.push({
        label: formatTimestamp(sample.observed_at, windowSeconds, withDate),
        value
      });
    }
    return { points: plotted, skipped: ignored };
  }, [trend, windowSeconds, withDate]);

  if (selectable.length === 0) {
    return (
      <Paper withBorder radius='md' p='md'>
        <Text c='dimmed'>{t`No mapped signals are available to chart.`}</Text>
      </Paper>
    );
  }

  const unit = trend?.unit ?? '';

  return (
    <Paper withBorder radius='md' p='md'>
      <Stack gap='sm'>
        <Group gap='sm' align='flex-end' wrap='wrap'>
          <Select
            label={t`Parameter`}
            searchable
            value={bindingId}
            onChange={setBindingId}
            data={selectable.map((signal) => ({
              value: String(signal.binding_id),
              label: signal.unit
                ? `${signal.display_name} (${signal.unit})`
                : signal.display_name
            }))}
            style={{ minWidth: 320 }}
          />
          <Select
            label={t`Range`}
            value={rangeSeconds}
            onChange={(value) => setRangeSeconds(value ?? '3600')}
            data={[
              ...RANGES.map((range) => ({
                value: range.value,
                label: range.label()
              })),
              { value: CUSTOM, label: t`Custom range…` }
            ]}
            allowDeselect={false}
            style={{ minWidth: 170 }}
          />
          {isCustom && (
            <>
              <DateTimePicker
                label={t`From`}
                value={customFrom}
                onChange={setCustomFrom}
                valueFormat='YYYY-MM-DD HH:mm:ss'
                withSeconds
                clearable
                maxDate={new Date()}
                placeholder={t`Start of window`}
                style={{ minWidth: 210 }}
              />
              <DateTimePicker
                label={t`To`}
                value={customTo}
                onChange={setCustomTo}
                valueFormat='YYYY-MM-DD HH:mm:ss'
                withSeconds
                clearable
                minDate={customFrom ?? undefined}
                placeholder={t`End of window`}
                style={{ minWidth: 210 }}
              />
            </>
          )}
          {trend?.available && (
            <Badge variant='light' color='gray'>
              {t`${points.length} samples`}
            </Badge>
          )}
        </Group>

        {rangeError && (
          <Alert color='gray' title={t`Choose a window to read`}>
            {rangeError}
          </Alert>
        )}

        {trendQuery.isLoading && <Loader size='sm' />}

        {trendQuery.isError && (
          <Alert color='red' title={t`Could not load the trend`}>
            {t`The request for this window failed. No data is shown rather than a partial line.`}
          </Alert>
        )}

        {trend && !trend.available && (
          <Alert color='gray' title={t`No history for this signal`}>
            {unavailableMessage(trend)}
          </Alert>
        )}

        {trend?.available && trend.truncated && (
          <Alert color='yellow' title={t`This window was truncated`}>
            {t`The source returned more than the ${trend.limits.max_samples} sample limit, so the most recent part of this window is not shown. Choose a shorter range to see it.`}
          </Alert>
        )}

        {trend?.available && skipped > 0 && (
          <Alert color='gray' title={t`Some samples are not plottable`}>
            {t`${skipped} of ${trend.samples.length} samples are not numeric and were left off the chart rather than coerced to a number.`}
          </Alert>
        )}

        {trend?.available && points.length === 0 && skipped === 0 && (
          <Alert color='gray' title={t`No samples in this window`}>
            {t`${trend.source_name} served this window but had no data in it. That is different from the source being unable to answer.`}
          </Alert>
        )}

        {trend?.available && points.length > 1 && (
          <AreaChart
            h={320}
            data={points}
            dataKey='label'
            series={[
              { name: 'value', label: trend.display_name, color: 'blue.6' }
            ]}
            curveType='linear'
            connectNulls={false}
            withDots={points.length <= 60}
            yAxisLabel={unit || undefined}
            xAxisProps={{ minTickGap: 32 }}
            valueFormatter={(value) =>
              unit ? `${value} ${unit}` : String(value)
            }
          />
        )}

        {trend?.available && points.length === 1 && (
          <Alert color='gray' title={t`Only one sample`}>
            {t`A single point is not a trend. Widen the range to see whether it is moving.`}
          </Alert>
        )}

        {trend?.available && (
          <Text size='xs' c='dimmed'>
            {t`Read live from ${trend.source_name}. AIMMS does not store this history.`}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}

export default SignalTrendChart;
