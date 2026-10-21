import type {
  MachineAnomaly,
  MachineHealthSummary,
  MachineSignal
} from '@lib/types/MachineHealth';
import { plural, t } from '@lingui/core/macro';
import { Button, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { useId } from 'react';
import { displayInstant } from '../displayClock';
import monitoring from '../monitoring.module.css';
import { conditionText } from './SignalCondition';
import { StatusBadge, statusTone } from './StatusBadge';
import {
  AttentionAlert,
  FreshnessText,
  HealthStateBadge,
  MetaText,
  Unit,
  formatSignalLimits
} from './common';
import { headlineFacts } from './headlineFacts';

/** Headline remains the server assessment, visibly bounded by its coverage. */
export function HealthSummaryPanel({
  summary,
  signals,
  anomalies = null,
  onSelect
}: Readonly<{
  summary: MachineHealthSummary;
  signals: MachineSignal[] | null;
  anomalies?: MachineAnomaly[] | null;
  onSelect?: (binding: number, trigger: HTMLElement) => void;
}>) {
  const id = useId();
  const fact = headlineFacts(summary, signals, anomalies);
  const { coverage, critical } = fact;
  const counts = summary.anomaly_counts;
  const newest = signals?.find(
    (s) => s.observed_at === summary.last_observed_at
  );
  const signalLink = (signal: MachineSignal) => (
    <Button
      variant='subtle'
      className={monitoring.link}
      size='compact-sm'
      px={0}
      h='auto'
      onClick={(event) => onSelect?.(signal.binding_id, event.currentTarget)}
    >
      {signal.display_name}
    </Button>
  );
  return (
    <SimpleGrid
      cols={{ base: 1, md: 2 }}
      spacing='sm'
      data-testid='health-summary'
    >
      <Stack gap={6}>
        <Group gap='sm' align='flex-start' style={{ maxWidth: 640 }}>
          <HealthStateBadge
            state={summary.state}
            size='lg'
            neutralNormal={fact.coverageIncomplete}
            describedBy={id}
          />
          <MetaText id={id}>
            {coverage
              ? t`Assessed on ${coverage.evaluated} of ${coverage.mapped} signals`
              : t`Signal coverage unavailable: signal read failed`}{' '}
            ·{' '}
            {critical
              ? critical.evaluated < critical.designated
                ? t`critical coverage incomplete: ${critical.evaluated} of ${critical.designated}`
                : t`critical coverage complete: ${critical.evaluated} of ${critical.designated}`
              : t`critical coverage unavailable`}
          </MetaText>
        </Group>
        {fact.coverageIncomplete && (
          <MetaText size='xs'>{t`Missing critical evidence cannot be ruled out, even when the assessment is Normal.`}</MetaText>
        )}
        {fact.noObservation && (
          <Text size='sm'>
            {plural(coverage!.mapped, {
              one: 'No observation from # mapped signal',
              other: 'No observation from # mapped signals'
            })}
          </Text>
        )}
        {fact.why === 'signals' && (
          <Stack gap={2} aria-label={t`Assessment cause`}>
            {fact.matchingSignals.map((s) => (
              <Text size='sm' key={s.binding_id}>
                {signalLink(s)}{' '}
                {s.value == null ? t`Unavailable` : String(s.value)}{' '}
                <Unit code={s.unit} /> · {conditionText(s)} (
                {formatSignalLimits(s) ?? t`Not configured`}) · {t`observed`}{' '}
                {displayInstant(s.observed_at) ?? t`Time unavailable`}
              </Text>
            ))}
          </Stack>
        )}
        {fact.why === 'anomaly' && (
          <Text size='sm'>{t`From active anomaly: ${fact.anomaly!.title}`}</Text>
        )}
        {fact.why === 'unavailable' && (
          <MetaText>{t`Cause not supplied by the summary`}</MetaText>
        )}
        <Group gap={6}>
          {counts ? (
            Object.values(counts).every((count) => count === 0) ? (
              <MetaText>{t`No active anomaly records`}</MetaText>
            ) : (
              (['critical', 'warning', 'info'] as const)
                .filter((severity) => counts[severity] > 0)
                .map((severity) => (
                  <StatusBadge
                    key={severity}
                    tone={statusTone(severity)}
                    color={
                      severity === 'critical'
                        ? 'red.9'
                        : severity === 'warning'
                          ? 'yellow.6'
                          : 'blue.7'
                    }
                    variant='filled'
                  >
                    {counts[severity]} {severity}
                  </StatusBadge>
                ))
            )
          ) : (
            <MetaText>{t`Active anomaly counts unavailable`}</MetaText>
          )}
        </Group>
        <MetaText size='xs'>{t`Last observation`}</MetaText>
        <FreshnessText
          observedAt={summary.last_observed_at}
          stale={newest?.stale ?? false}
          thresholdSeconds={newest?.freshness_threshold_seconds}
        />
        <MetaText size='xs'>{t`Backend assessment · assessment time unavailable`}</MetaText>
      </Stack>
      <Stack gap={4}>
        {critical && critical.evaluated === critical.designated ? (
          <MetaText>{t`Critical coverage: ${critical.evaluated} of ${critical.designated} designated signals evaluated now`}</MetaText>
        ) : (
          <AttentionAlert
            title={
              critical
                ? t`Critical coverage incomplete`
                : t`Critical coverage unavailable`
            }
          >
            {fact.criticalMissing.map(({ signal, reason }) => (
              <Text key={signal.binding_id} size='sm'>
                {signalLink(signal)} · {reason}
              </Text>
            ))}
            {!critical && (
              <Text size='sm'>{t`No critical-signal designation is supplied. Critical limits do not designate critical signals.`}</Text>
            )}
            <MetaText size='xs'>{t`Coverage is limited to engineer-designated bindings. Unassessed or missing evidence remains unknown; coverage does not establish equipment health.`}</MetaText>
          </AttentionAlert>
        )}
        {summary.state === 'offline' && (
          <MetaText>{t`Every mapped signal is older than its source's freshness budget. Values are historical, not the machine's current state.`}</MetaText>
        )}
        {summary.degraded_data && summary.state !== 'offline' && (
          <AttentionAlert
            title={t`Degraded data`}
          >{t`Some signals are stale or reported with poor quality. Confirm the readings you rely on before acting on them.`}</AttentionAlert>
        )}
      </Stack>
    </SimpleGrid>
  );
}
export default HealthSummaryPanel;
