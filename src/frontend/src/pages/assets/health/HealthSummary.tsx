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

import type {
  MachineHealthSummary,
  MachineSignal
} from '@lib/types/MachineHealth';

import { HealthStateBadge, ObservedAt } from './common';
import { healthCoverage } from './healthCoverage';

/**
 * Current condition for one machine.
 *
 * Freshness and data quality sit next to the state rather than behind it: a
 * critical decision must never be made against telemetry that has quietly gone
 * stale, so degraded data is called out even when the overall state looks calm.
 */
export function HealthSummaryPanel({
  summary,
  signals
}: Readonly<{
  summary: MachineHealthSummary;
  signals: MachineSignal[] | null;
}>) {
  const counts = summary.anomaly_counts;
  const coverage = signals ? healthCoverage(signals) : null;
  const newestRows = signals?.filter(
    (signal) =>
      signal.observed_at &&
      summary.last_observed_at &&
      Date.parse(signal.observed_at) === Date.parse(summary.last_observed_at)
  );

  return (
    <Stack gap='sm'>
      <Paper withBorder radius='md' p='md'>
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing='md'>
          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Condition`}
            </Text>
            <HealthStateBadge state={summary.state} size='lg' />
            <Text
              size='xs'
              c='dimmed'
            >{t`Current backend assessment of mapped signals and active machine anomalies. Assessment time not supplied.`}</Text>
          </Stack>

          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Last observation`}
            </Text>
            <ObservedAt
              observedAt={summary.last_observed_at}
              stale={newestRows?.some((signal) => signal.stale) ?? false}
            />
            {summary.last_observed_at && !newestRows?.length && (
              <Text
                size='xs'
                c='dimmed'
              >{t`Observation freshness unavailable`}</Text>
            )}
          </Stack>

          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Active anomalies on this machine`}
            </Text>
            <Group gap='xs'>
              <Badge color='red' variant='light'>
                {t`Critical: ${counts?.critical ?? t`Unavailable`}`}
              </Badge>
              <Badge color='yellow' variant='light'>
                {t`Warning: ${counts?.warning ?? t`Unavailable`}`}
              </Badge>
              <Badge color='blue' variant='light'>
                {t`Info: ${counts?.info ?? t`Unavailable`}`}
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

      <Paper withBorder radius='md' p='md'>
        <Stack gap='xs'>
          <Text size='sm' fw={600}>{t`Observation and coverage`}</Text>
          {coverage ? (
            <Text size='sm'>{t`${coverage.mapped} mapped · ${coverage.usable} usable now · ${coverage.evaluated} evaluated now`}</Text>
          ) : (
            <Text size='sm'>{t`Signal coverage unavailable: signal read failed`}</Text>
          )}
          <Text
            size='xs'
            c='dimmed'
          >{t`Usable means a current observation with good source quality. Evaluated means the server classified that signal against configured limits. Neither establishes overall equipment health.`}</Text>
          {coverage?.criticalCoverage ? (
            <Alert
              color={
                coverage.criticalCoverage.evaluated <
                coverage.criticalCoverage.designated
                  ? 'yellow'
                  : 'blue'
              }
              title={
                coverage.criticalCoverage.evaluated <
                coverage.criticalCoverage.designated
                  ? t`Critical coverage incomplete`
                  : t`Designated critical coverage`
              }
            >
              <Text size='sm'>{t`${coverage.criticalCoverage.designated} designated · ${coverage.criticalCoverage.usable} usable now · ${coverage.criticalCoverage.evaluated} evaluated now`}</Text>
              <Text size='sm'>{t`Coverage is limited to engineer-designated bindings. Unassessed or missing evidence remains unknown; coverage does not establish equipment health.`}</Text>
            </Alert>
          ) : (
            <Alert color='yellow' title={t`Critical coverage unavailable`}>
              {t`No critical-signal designation is supplied. Critical limits do not identify which signals are critical. Missing critical evidence cannot be ruled out, even when the current assessment is Normal.`}
            </Alert>
          )}
        </Stack>
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
