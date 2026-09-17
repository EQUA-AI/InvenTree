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
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { MachineSignal, SignalTrend } from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';

/**
 * Preset look-back windows, in seconds.
 *
 * Capped at the server's 30-day maximum. Offering a range the server will
 * silently shrink would make the axis lie about which window was actually read.
 */
const RANGES = [
  { value: '3600', label: () => t`Last hour` },
  { value: '21600', label: () => t`Last 6 hours` },
  { value: '86400', label: () => t`Last 24 hours` },
  { value: '604800', label: () => t`Last 7 days` },
  { value: '2592000', label: () => t`Last 30 days` }
];

/** Values the historian can return that cannot be plotted on a numeric axis. */
function numericValue(value: unknown): number | null {
  if (value === null || value === undefined || typeof value === 'boolean') {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatTimestamp(iso: string, windowSeconds: number): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  // A 7-day window labelled only with clock times repeats itself every 24h and
  // becomes unreadable; a one-hour window does not need the date.
  return windowSeconds > 86400
    ? date.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    : date.toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
        second: windowSeconds <= 3600 ? '2-digit' : undefined
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
 * * **Hide truncation.** The server returns at most 2000 samples and drops the
 *   *newest* ones when a window overflows, because `read_window` fills from the
 *   oldest bucket forward and stops. A chart that concealed that would be
 *   asserting the plant did nothing during the missing period.
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
  const [rangeSeconds, setRangeSeconds] = useState<string>('86400');

  const windowSeconds = Number(rangeSeconds);

  const trendQuery = useQuery<SignalTrend>({
    queryKey: [
      'machine-health-trend-chart',
      machineId,
      bindingId,
      rangeSeconds
    ],
    enabled: bindingId != null,
    staleTime: 60 * 1000,
    queryFn: async () => {
      const end = new Date();
      const start = new Date(end.getTime() - windowSeconds * 1000);
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_trend, machineId),
        {
          params: {
            binding: bindingId,
            from: start.toISOString(),
            to: end.toISOString()
          }
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
        label: formatTimestamp(sample.observed_at, windowSeconds),
        value
      });
    }
    return { points: plotted, skipped: ignored };
  }, [trend, windowSeconds]);

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
            onChange={(value) => setRangeSeconds(value ?? '86400')}
            data={RANGES.map((range) => ({
              value: range.value,
              label: range.label()
            }))}
            allowDeselect={false}
            style={{ minWidth: 170 }}
          />
          {trend?.available && (
            <Badge variant='light' color='gray'>
              {t`${points.length} samples`}
            </Badge>
          )}
        </Group>

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
