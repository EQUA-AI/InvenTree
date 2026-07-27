import { t } from '@lingui/core/macro';
import { Group, Paper, Stack, Table, Text } from '@mantine/core';

import type { MachineSignal } from '@lib/types/MachineHealth';

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
export function SignalTable({
  signals,
  machineId
}: Readonly<{ signals: MachineSignal[]; machineId: number }>) {
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
