import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';
import { IconAlertTriangle, IconPlugConnectedX } from '@tabler/icons-react';

import type { MachineHealthSummary } from '@lib/types/MachineHealth';

import { HealthStateBadge, ObservedAt } from './common';

/**
 * Current condition for one machine.
 *
 * Freshness and data quality sit next to the state rather than behind it: a
 * critical decision must never be made against telemetry that has quietly gone
 * stale, so degraded data is called out even when the overall state looks calm.
 */
export function HealthSummaryPanel({
  summary
}: Readonly<{ summary: MachineHealthSummary }>) {
  const counts = summary.anomaly_counts ?? {
    critical: 0,
    warning: 0,
    info: 0
  };

  return (
    <Stack gap='sm'>
      <Paper withBorder radius='md' p='md'>
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing='md'>
          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Condition`}
            </Text>
            <HealthStateBadge state={summary.state} size='lg' />
          </Stack>

          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Last observation`}
            </Text>
            <ObservedAt
              observedAt={summary.last_observed_at}
              stale={summary.stale_signal_count > 0}
            />
          </Stack>

          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Active anomalies`}
            </Text>
            <Group gap='xs'>
              <Badge color='red' variant='light'>
                {t`Critical: ${counts.critical ?? 0}`}
              </Badge>
              <Badge color='yellow' variant='light'>
                {t`Warning: ${counts.warning ?? 0}`}
              </Badge>
              <Badge color='blue' variant='light'>
                {t`Info: ${counts.info ?? 0}`}
              </Badge>
            </Group>
          </Stack>

          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Signals`}
            </Text>
            <Text size='sm'>
              {summary.stale_signal_count > 0
                ? t`${summary.signal_count} mapped, ${summary.stale_signal_count} stale`
                : t`${summary.signal_count} mapped`}
            </Text>
          </Stack>
        </SimpleGrid>
      </Paper>

      {summary.state === 'offline' && (
        <Alert
          color='gray'
          variant='light'
          icon={<IconPlugConnectedX size={16} />}
          title={t`No current data`}
        >
          {t`Every mapped signal for this machine is older than its source's freshness budget. Treat the values below as historical, not as the machine's current state.`}
        </Alert>
      )}

      {summary.degraded_data && summary.state !== 'offline' && (
        <Alert
          color='yellow'
          variant='light'
          icon={<IconAlertTriangle size={16} />}
          title={t`Degraded data`}
        >
          {t`Some signals are stale or reported with poor quality. Confirm the readings you rely on before acting on them.`}
        </Alert>
      )}
    </Stack>
  );
}

export default HealthSummaryPanel;
