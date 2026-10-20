import { t } from '@lingui/core/macro';
import {
  Box,
  Group,
  Loader,
  Text,
  Tooltip,
  UnstyledButton
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import monitoring from '../monitoring.module.css';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { SignalTrend } from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';
import { prepareTrend, sparklinePath } from './trendSamples';

const WIDTH = 120;
const HEIGHT = 28;

/**
 * Look-back for a sparkline, in seconds.
 *
 * Asked for explicitly rather than left to the server's default. A sparkline is
 * 120px wide, so it cannot render more than ~120 distinct points, and every
 * sample behind it costs the connector a whole-station snapshot fetched and
 * parsed to extract one tag. Several of these render per machine page.
 *
 * The *window* is what gets shortened, never `max_samples`. Capping the sample
 * count would look equivalent and is not: `read_window` fills from the oldest
 * bucket forward and stops, so a cap drops the *newest* readings. A sparkline
 * trimmed that way would show the oldest ten minutes of its window and hide the
 * most recent - precisely backwards for a "recent trend". Ten minutes at the
 * five-second source cadence is ~120 samples, which is the width of the line.
 */
const SPARKLINE_WINDOW_SECONDS = 10 * 60;

/**
 * A sparkline for one mapped signal — but only when the source can actually
 * serve one.
 *
 * The plan is explicit that sparklines appear "only when bounded trend data is
 * available". A source with no connector, or one that cannot read history, gets
 * a short explanation instead of a line: a chart drawn from a single current
 * value would imply a trend nobody measured.
 *
 * The request names the *binding*, never the external tag, so a client cannot
 * reach an arbitrary point in the source system.
 */
export function SignalTrendSparkline({
  machineId,
  bindingId,
  enabled = true,
  trend: supplied,
  supplierPending = false,
  supplierFailed = false
}: Readonly<{
  machineId: number;
  bindingId: number;
  enabled?: boolean;
  /**
   * A trend already fetched for this binding, normally by the signal table
   * reading the whole page in one request. When a supplier owns this binding no
   * request is made here: a table of thirty sparklines each fetching its own
   * window makes the source read and parse the same documents thirty times
   * over.
   *
   * `null` means a supplier owns the binding but has nothing for it - because
   * its batch is still in flight, or because it came back without this one.
   * That is still a supplier, so it must suppress the local fetch exactly like
   * a delivered trend does. Only `undefined` - nobody is supplying - lets this
   * sparkline read for itself.
   */
  trend?: SignalTrend | null;
  /**
   * The supplier's batched request is still in flight, so show a loader rather
   * than the "no trend" it would otherwise be indistinguishable from.
   */
  supplierPending?: boolean;
  /**
   * The supplier's batched request failed. A failed read is not an absence
   * of data, so it says so rather than reading "No trend".
   */
  supplierFailed?: boolean;
}>) {
  const api = useApi();

  // `undefined` is the only value that means nobody is supplying a trend.
  // Testing for it directly here is what went wrong before: the table hands
  // every row `map.get(id)`, which is `undefined` for all of them until the
  // batch resolves, so every sparkline fired its own request first and the
  // batch saved nothing.
  const managed = supplied !== undefined;

  const trendQuery = useQuery<SignalTrend>({
    queryKey: ['machine-health-trend', machineId, bindingId],
    // Only when nobody is supplying one; a lone sparkline outside the table
    // still works on its own.
    enabled: enabled && !managed,
    // Trends are a federated read against the historian; don't re-fetch them on
    // every focus change.
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const end = new Date();
      const start = new Date(end.getTime() - SPARKLINE_WINDOW_SECONDS * 1000);
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_trend, machineId),
        {
          params: {
            binding: bindingId,
            from: start.toISOString(),
            to: end.toISOString()
          },
          // A historian read does not fit the global 5s default.
          timeout: 30 * 1000
        }
      );
      return response.data;
    }
  });

  // One source of truth for the rest of the component: whatever the table
  // supplied, or this sparkline's own fetch when it is standing alone.
  const trend = managed ? supplied : trendQuery.data;

  // The shared reading: every sample at its own time, a break after any
  // reading that cannot be drawn and across any silence longer than the
  // source's cadence (R6 A05.9). The old line spaced samples by index and
  // dropped unreadable ones first, so a silence of minutes drew as one step
  // and the readings either side of it were joined.
  const prepared = useMemo(() => prepareTrend(trend), [trend]);
  const path = useMemo(
    () => sparklinePath(prepared, WIDTH, HEIGHT),
    [prepared]
  );

  if (!enabled) {
    return null;
  }

  if (managed ? supplierPending : trendQuery.isLoading) {
    return <Loader size='xs' />;
  }

  if (managed ? supplierFailed && !supplied : trendQuery.isError) {
    return (
      <TrendStatus
        label={t`Read failed`}
        detail={t`The trend read failed. The current reading is unaffected.`}
      />
    );
  }

  if (!trend) {
    return (
      <TrendStatus
        label={t`Not returned`}
        detail={t`The completed trend read did not return this signal. Its history is unknown; the current reading is unaffected.`}
      />
    );
  }

  if (!trend.available) {
    const label =
      trend.reason === 'SOURCE_UNAVAILABLE' || trend.reason === 'SOURCE_TIMEOUT'
        ? t`Read failed`
        : trend.reason === 'NO_CONNECTOR'
          ? t`No trend`
          : trend.reason === 'HISTORY_UNSUPPORTED'
            ? t`No history`
            : t`History unavailable`;
    return (
      <TrendStatus
        label={label}
        detail={
          trend.detail ?? t`The source could not serve history for this signal.`
        }
      />
    );
  }

  if (prepared.measured === 0 && prepared.undated === 0) {
    return (
      <TrendStatus
        label={t`No samples`}
        detail={t`${trend.source_name} answered for this window but held no readings for this signal.`}
      />
    );
  }

  if (path === null) {
    const label =
      prepared.drawn === 0
        ? t`No drawable readings`
        : prepared.measured > 1
          ? t`1 of ${prepared.measured} drawable`
          : t`Only one reading`;
    return (
      <TrendStatus
        label={label}
        detail={t`${prepared.drawn} of ${prepared.measured} timed readings in this window can be drawn; ${prepared.undated} readings have no readable time.`}
      />
    );
  }

  const breaks = prepared.silences + prepared.notNumeric + prepared.notGood;

  return (
    <Tooltip
      label={
        breaks > 0
          ? t`${prepared.measured} samples from ${trend.source_name}. The line breaks where the source was silent or a reading could not be drawn.`
          : t`${prepared.measured} samples from ${trend.source_name}`
      }
      multiline={breaks > 0}
      w={breaks > 0 ? 260 : undefined}
    >
      <Group gap={4} wrap='nowrap'>
        <Box
          component='svg'
          width={WIDTH}
          height={HEIGHT}
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role='img'
          aria-label={t`Recent trend for ${trend.display_name}`}
        >
          <path
            d={path}
            fill='none'
            stroke='currentColor'
            strokeWidth={1.5}
            strokeLinejoin='round'
            strokeLinecap='round'
            opacity={0.75}
          />
        </Box>
        {trend.truncated && (
          <Text size='xs' className={monitoring.meta}>
            {t`(trimmed)`}
          </Text>
        )}
      </Group>
    </Tooltip>
  );
}

/** Visible state and a reason available on keyboard focus and touch. */
function TrendStatus({
  label,
  detail
}: Readonly<{ label: string; detail: string }>) {
  return (
    <Tooltip
      label={detail}
      multiline
      w={260}
      events={{ hover: true, focus: true, touch: true }}
    >
      <UnstyledButton
        fz='xs'
        className={monitoring.meta}
        aria-label={`${label}: ${detail}`}
      >
        {label}
      </UnstyledButton>
    </Tooltip>
  );
}

export default SignalTrendSparkline;
