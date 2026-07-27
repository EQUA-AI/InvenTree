import { t } from '@lingui/core/macro';
import { Box, Group, Loader, Text, Tooltip } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { SignalTrend } from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';

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

  const path = useMemo(() => {
    const samples = (trendQuery.data?.samples ?? [])
      .map((sample) => Number(sample.value))
      .filter((value) => Number.isFinite(value));

    if (samples.length < 2) {
      return null;
    }

    // Samples arrive newest-first from the historian; draw them left-to-right.
    const ordered = [...samples].reverse();
    const min = Math.min(...ordered);
    const max = Math.max(...ordered);
    const span = max - min || 1;
    const step = WIDTH / (ordered.length - 1);

    return ordered
      .map((value, index) => {
        const x = index * step;
        const y = HEIGHT - ((value - min) / span) * HEIGHT;
        return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  }, [trendQuery.data]);

  if (!enabled) {
    return null;
  }

  if (trendQuery.isLoading) {
    return <Loader size='xs' />;
  }

  const trend = trendQuery.data;

  if (!trend?.available) {
    return (
      <Tooltip label={trend?.detail ?? t`No trend data is available.`}>
        <Text size='xs' c='dimmed'>
          {t`No trend`}
        </Text>
      </Tooltip>
    );
  }

  if (path === null) {
    return (
      <Text size='xs' c='dimmed'>
        {t`Too few samples`}
      </Text>
    );
  }

  return (
    <Tooltip
      label={t`${trend.samples.length} samples from ${trend.source_name}`}
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
          <Text size='xs' c='dimmed'>
            {t`(trimmed)`}
          </Text>
        )}
      </Group>
    </Tooltip>
  );
}

export default SignalTrendSparkline;
