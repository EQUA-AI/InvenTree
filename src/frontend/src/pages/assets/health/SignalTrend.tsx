import { t } from '@lingui/core/macro';
import { Box, Group, Loader, Tooltip } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import {
  FailedValue,
  MissingValue
} from '../../../components/items/ValueTooltip';
import { MetaText } from './common';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { SignalTrend } from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';
import { formatInstant, useDisplayClock } from './mimic/observedTime';
import { trendGeometry } from './trendGeometry';
import { type TrendStatusFact, trendStatusFact } from './trendPresentation';
import { prepareTrend } from './trendSamples';

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
  supplierFailed = false,
  focusable = true
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
  /** A dense table exposes this same fact through its row-info control. */
  focusable?: boolean;
}>) {
  const api = useApi();
  const clock = useDisplayClock();

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
  const geometry = useMemo(
    () => trendGeometry(trend?.samples ?? [], WIDTH, HEIGHT, prepared),
    [trend, prepared]
  );

  if (!enabled) {
    return null;
  }

  if (managed ? supplierPending : trendQuery.isLoading) {
    return <Loader size='xs' />;
  }

  const status = trendStatusFact(trend, {
    failed: managed ? supplierFailed && !supplied : trendQuery.isError,
    prepared
  });
  if (status) return <TrendStatus fact={status} focusable={focusable} />;
  // A successful status means there are at least two drawable observations.
  if (!trend || geometry === null) return null;

  const windowText = `${formatInstant(trend.window_start, clock) ?? '—'} – ${formatInstant(trend.window_end, clock) ?? '—'} (${clock.label})`;
  const scaleText = `${geometry.min.toLocaleString()}–${geometry.max.toLocaleString()} ${trend.unit}`;
  const summary = t`Recent trend for ${trend.display_name}: ${scaleText}. ${windowText}. Source: ${trend.source_name}. ${geometry.excluded} excluded readings; ${geometry.uncertain} uncertain, ${geometry.bad} bad.`;
  return (
    <Tooltip
      events={{ hover: true, focus: true, touch: false }}
      label={summary}
      multiline
      w={320}
    >
      <Group
        gap={4}
        wrap='nowrap'
        tabIndex={focusable ? 0 : undefined}
        aria-label={focusable ? summary : undefined}
      >
        <Box
          component='svg'
          width={WIDTH}
          height={HEIGHT}
          viewBox={`-2 -2 ${WIDTH + 4} ${HEIGHT + 4}`}
          role='img'
          aria-label={summary}
        >
          <path
            d={geometry.path}
            fill='none'
            stroke='var(--mantine-color-blue-6)'
            strokeWidth={1.5}
            strokeLinejoin='round'
            strokeLinecap='round'
            opacity={0.75}
          />
          {geometry.points.map((point, index) => (
            <circle
              key={index}
              cx={point.x}
              cy={point.y}
              r={1.5}
              fill='var(--mantine-color-blue-6)'
            />
          ))}
        </Box>
        {trend.truncated && <MetaText size='xs'>{t`(trimmed)`}</MetaText>}
      </Group>
    </Tooltip>
  );
}

/** Visible state and a reason available on keyboard focus and touch. */
function TrendStatus({
  fact,
  focusable
}: Readonly<{ fact: TrendStatusFact; focusable: boolean }>) {
  return fact.failed ? (
    <FailedValue reason={fact.detail} focusable={focusable} />
  ) : (
    <MissingValue
      reason={`${fact.label}: ${fact.detail}`}
      focusable={focusable}
    />
  );
}

export default SignalTrendSparkline;
