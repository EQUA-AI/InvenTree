import type {
  MachineHealthSummary,
  MachineSignal
} from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { Alert, Group, Paper, SimpleGrid, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconPlugConnectedX } from '@tabler/icons-react';
import monitoring from '../monitoring.module.css';
import { StatusBadge } from './StatusBadge';
import { HealthStateBadge, ObservedAt } from './common';
import { healthCoverage } from './healthCoverage';

/** Assessment, quality and designated coverage remain distinct facts. */
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
  const critical = coverage?.criticalCoverage;
  return (
    <Stack gap='sm'>
      <Paper withBorder p='sm'>
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing='md'>
          <Stack gap='xs'>
            <Group justify='space-between' align='flex-start'>
              <Stack gap={4}>
                <Text
                  size='sm'
                  className={monitoring.meta}
                >{t`Condition`}</Text>
                <HealthStateBadge state={summary.state} size='lg' />
              </Stack>
              <Stack gap={4}>
                <Text
                  size='sm'
                  className={monitoring.meta}
                >{t`Last observation`}</Text>
                <ObservedAt
                  observedAt={summary.last_observed_at}
                  stale={newestRows?.some((signal) => signal.stale) ?? false}
                />
                {summary.last_observed_at && !newestRows?.length && (
                  <Text
                    size='sm'
                    className={monitoring.meta}
                  >{t`Observation freshness unavailable`}</Text>
                )}
              </Stack>
            </Group>
            <Text
              size='sm'
              className={monitoring.meta}
            >{t`Backend assessment · assessment time unavailable`}</Text>
            <Text size='sm'>
              {summary.stale_signal_count > 0
                ? t`${summary.signal_count} mapped, ${summary.stale_signal_count} stale`
                : t`${summary.signal_count} mapped`}
            </Text>
            <Text
              size='sm'
              className={monitoring.meta}
            >{t`Active anomalies on this machine`}</Text>
            <Group gap={6}>
              <StatusBadge color='red'>{t`Critical: ${counts?.critical ?? t`Unavailable`}`}</StatusBadge>
              <StatusBadge color='yellow'>{t`Warning: ${counts?.warning ?? t`Unavailable`}`}</StatusBadge>
              <StatusBadge color='blue'>{t`Info: ${counts?.info ?? t`Unavailable`}`}</StatusBadge>
            </Group>
          </Stack>
          <Stack gap={4}>
            <Text size='sm' fw={600}>{t`Observation and coverage`}</Text>
            <Text size='sm'>
              {coverage
                ? t`${coverage.mapped} mapped · ${coverage.usable} usable now · ${coverage.evaluated} evaluated now`
                : t`Signal coverage unavailable: signal read failed`}
            </Text>
            <Text
              size='sm'
              className={monitoring.meta}
              maw='70ch'
            >{t`Usable: current, good-quality observation. Evaluated: classified by the server against configured limits.`}</Text>
            {critical ? (
              <Alert
                classNames={
                  critical.evaluated < critical.designated
                    ? { label: monitoring.attentionTitle }
                    : undefined
                }
                color={
                  critical.evaluated < critical.designated ? 'yellow' : 'blue'
                }
                maw='70ch'
                title={
                  critical.evaluated < critical.designated
                    ? t`Critical coverage incomplete`
                    : t`Designated critical coverage`
                }
              >
                <Text size='sm'>{t`${critical.designated} designated · ${critical.usable} usable now · ${critical.evaluated} evaluated now`}</Text>
                <Text size='sm'>{t`Coverage is limited to engineer-designated bindings. Unassessed or missing evidence remains unknown; coverage does not establish equipment health.`}</Text>
              </Alert>
            ) : (
              <Alert
                color='yellow'
                classNames={{ label: monitoring.attentionTitle }}
                title={t`Critical coverage unavailable`}
                maw='70ch'
              >
                {t`No critical-signal designation is supplied. Critical limits do not designate critical signals; missing critical evidence cannot be ruled out.`}
              </Alert>
            )}
          </Stack>
        </SimpleGrid>
      </Paper>
      {summary.state === 'offline' && (
        <Alert
          color='gray'
          icon={<IconPlugConnectedX size={16} />}
          title={t`No current data`}
          maw='70ch'
        >
          {t`Every mapped signal for this machine is older than its source's freshness budget. Treat the values below as historical, not as the machine's current state.`}
        </Alert>
      )}
      {summary.degraded_data && summary.state !== 'offline' && (
        <Alert
          color='yellow'
          icon={<IconAlertTriangle size={16} />}
          classNames={{ label: monitoring.attentionTitle }}
          title={t`Degraded data`}
          maw='70ch'
        >
          {t`Some signals are stale or reported with poor quality. Confirm the readings you rely on before acting on them.`}
        </Alert>
      )}
    </Stack>
  );
}
export default HealthSummaryPanel;
