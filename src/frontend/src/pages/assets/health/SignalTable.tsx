import { t } from '@lingui/core/macro';
import { Group, Paper, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { MachineSignal, SignalTrend } from '@lib/types/MachineHealth';

import { useApi } from '../../../contexts/ApiContext';

import { SignalTrendSparkline } from './SignalTrend';
import {
  HealthStateBadge,
  ObservedAt,
  SourceTypeBadge,
  qualityLabel
} from './common';

function formatValue(signal: MachineSignal): string {
  if (signal.value === null || signal.value === undefined) {
    return '—';
  }
  const value =
    typeof signal.value === 'number'
      ? Number.parseFloat(signal.value.toFixed(3)).toString()
      : String(signal.value);
  return signal.unit ? `${value} ${signal.unit}` : value;
}

function formatLimits(signal: MachineSignal): string {
  const { warn_min, warn_max, critical_min, critical_max } = signal.limits;
  const parts: string[] = [];
  if (warn_min !== null || warn_max !== null) {
    parts.push(t`Warn ${warn_min ?? '−∞'}…${warn_max ?? '∞'}`);
  }
  if (critical_min !== null || critical_max !== null) {
    parts.push(t`Critical ${critical_min ?? '−∞'}…${critical_max ?? '∞'}`);
  }
  return parts.join(' · ');
}

/**
 * Mapped signals with their current value, provenance and freshness.
 *
 * A stale row still shows its last value - hiding it would lose information -
 * but its state reads Unknown rather than Normal, so an old number can never be
 * mistaken for a healthy machine.
 */
/** The sparkline window, matched to the one the standalone sparkline uses. */
const SPARKLINE_WINDOW_SECONDS = 10 * 60;

export function SignalTable({
  signals,
  machineId
}: Readonly<{ signals: MachineSignal[]; machineId: number }>) {
  const api = useApi();

  const bindingIds = useMemo(
    () => signals.map((signal) => signal.binding_id).filter(Boolean),
    [signals]
  );

  // One federated read for the whole table rather than one per row. Each
  // sparkline used to fetch its own window, and because a snapshot is a
  // whole-station document the source then read and parsed the same documents
  // once per row - about thirty times on a pump, seventy on some. Measured at
  // 6.4s per row against 6.7s for the entire table.
  const trendsQuery = useQuery<{ results: SignalTrend[] }>({
    queryKey: ['machine-health-trends', machineId, bindingIds],
    enabled: bindingIds.length > 0,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const end = new Date();
      const start = new Date(end.getTime() - SPARKLINE_WINDOW_SECONDS * 1000);
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_trends, machineId),
        {
          params: {
            bindings: bindingIds.join(','),
            from: start.toISOString(),
            to: end.toISOString()
          },
          // The global default is 5s, which a federated read of a historian
          // window does not fit in - measured at about 5s server-side for a
          // wide table before the network. Left at the default, every one of
          // these would time out and then retry, which is what made this page
          // fail rather than merely be slow.
          timeout: 60 * 1000
        }
      );
      return response.data;
    }
  });

  const trendByBinding = useMemo(() => {
    const map = new Map<number, SignalTrend>();
    for (const trend of trendsQuery.data?.results ?? []) {
      map.set(trend.binding_id, trend);
    }
    return map;
  }, [trendsQuery.data]);

  if (signals.length === 0) {
    return (
      <Paper withBorder radius='md' p='md'>
        <Text c='dimmed'>{t`No signals are mapped for this machine.`}</Text>
      </Paper>
    );
  }

  return (
    <Paper withBorder radius='md' p={0} style={{ overflowX: 'auto' }}>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Signal`}</Table.Th>
            <Table.Th>{t`Value`}</Table.Th>
            <Table.Th>{t`State`}</Table.Th>
            <Table.Th>{t`Observed`}</Table.Th>
            <Table.Th>{t`Trend`}</Table.Th>
            <Table.Th>{t`Quality`}</Table.Th>
            <Table.Th>{t`Source`}</Table.Th>
            <Table.Th>{t`Limits`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {signals.map((signal) => (
            <Table.Tr key={signal.binding_id}>
              <Table.Td>
                <Stack gap={0}>
                  <Text size='sm'>{signal.display_name}</Text>
                  {signal.signal_kind && (
                    <Text size='xs' c='dimmed'>
                      {signal.signal_kind}
                    </Text>
                  )}
                </Stack>
              </Table.Td>
              <Table.Td>
                <Text size='sm' fw={500}>
                  {formatValue(signal)}
                </Text>
              </Table.Td>
              <Table.Td>
                <HealthStateBadge state={signal.state} size='sm' />
              </Table.Td>
              <Table.Td>
                <ObservedAt
                  observedAt={signal.observed_at}
                  stale={signal.stale}
                />
              </Table.Td>
              <Table.Td>
                <SignalTrendSparkline
                  machineId={machineId}
                  bindingId={signal.binding_id}
                  trend={trendByBinding.get(signal.binding_id)}
                />
              </Table.Td>
              <Table.Td>
                <Text size='sm'>{qualityLabel(signal.quality)}</Text>
              </Table.Td>
              <Table.Td>
                <Group gap={6} wrap='nowrap'>
                  <SourceTypeBadge type={signal.source_type} />
                  <Text size='xs' c='dimmed'>
                    {signal.source_name}
                  </Text>
                </Group>
              </Table.Td>
              <Table.Td>
                <Text size='xs' c='dimmed'>
                  {formatLimits(signal) || t`Not configured`}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Paper>
  );
}

export default SignalTable;
