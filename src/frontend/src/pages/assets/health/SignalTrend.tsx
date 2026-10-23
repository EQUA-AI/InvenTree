import { t } from '@lingui/core/macro';
import { Box, Group, Loader, Text, Tooltip } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { SignalTrend } from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';
import { trendGeometry } from './trendGeometry';

const WIDTH = 120;
const HEIGHT = 28;

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
  enabled = true
}: Readonly<{ machineId: number; bindingId: number; enabled?: boolean }>) {
  const api = useApi();

  const trendQuery = useQuery<SignalTrend>({
    queryKey: ['machine-health-trend', machineId, bindingId],
    enabled,
    // Trends are a federated read against the historian; don't re-fetch them on
    // every focus change.
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_trend, machineId),
        { params: { binding: bindingId } }
      );
      return response.data;
    }
  });

  const geometry = useMemo(
    () => trendGeometry(trendQuery.data?.samples ?? [], WIDTH, HEIGHT),
    [trendQuery.data]
  );

  if (!enabled) {
    return null;
  }

  if (trendQuery.isLoading) {
    return <Loader size='xs' />;
  }

  const trend = trendQuery.data;

  if (trendQuery.isError) {
    return (
      <Text
        component='output'
        size='xs'
        c='red'
      >{t`Trend request failed`}</Text>
    );
  }

  if (!trend?.available) {
    return (
      <Tooltip label={trend?.detail ?? t`No trend data is available.`}>
        <Text size='xs' c='dimmed'>
          {t`No trend`}
        </Text>
      </Tooltip>
    );
  }

  if (geometry === null || geometry.points.length < 2) {
    return (
      <Text size='xs' c='dimmed'>
        {t`Too few samples`}
      </Text>
    );
  }

  const windowText = `${new Date(trend.window_start).toLocaleString()} – ${new Date(trend.window_end).toLocaleString()} (${Intl.DateTimeFormat().resolvedOptions().timeZone})`;
  const scaleText = `${geometry.min.toLocaleString()}–${geometry.max.toLocaleString()} ${trend.unit}`;
  const summary = t`Recent trend for ${trend.display_name}: ${scaleText}. ${windowText}. Source: ${trend.source_name}. ${geometry.excluded} excluded readings; ${geometry.uncertain} uncertain, ${geometry.bad} bad.`;
  return (
    <Tooltip label={summary} multiline w={320}>
      <Box tabIndex={0} aria-label={summary}>
        <Group gap={4} wrap='nowrap'>
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
              stroke='currentColor'
              strokeWidth={1.5}
              strokeLinejoin='round'
              strokeLinecap='round'
            />
            {geometry.points.map((point, index) => (
              <circle
                key={index}
                cx={point.x}
                cy={point.y}
                r={1.5}
                fill='currentColor'
              />
            ))}
          </Box>
          {trend.truncated && <Text size='xs' c='dimmed'>{t`(trimmed)`}</Text>}
        </Group>
        <Text size='xs' c='dimmed'>
          {t`Local range`}: {scaleText}
        </Text>
        {geometry.excluded > 0 && (
          <Text
            size='xs'
            c='orange'
          >{t`${geometry.excluded} missing or poor-quality readings`}</Text>
        )}
      </Box>
    </Tooltip>
  );
}

export default SignalTrendSparkline;
