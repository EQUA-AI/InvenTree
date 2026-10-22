import type {
  MachineAnomaly,
  MachineHealthSummary,
  MachineSignal
} from '@lib/types/MachineHealth';
import { plural, t } from '@lingui/core/macro';
import { Button, Group, Stack, Text } from '@mantine/core';
import {
  FailedValue,
  MissingValue,
  ValueTooltip
} from '../../../components/items/ValueTooltip';
import monitoring from '../monitoring.module.css';
import { recordedTelemetryAsLive } from '../telemetryPresentation';
import { conditionText } from './SignalCondition';
import { SignalValue } from './SignalReading';
import { StatusBadge, statusTone } from './StatusBadge';
import { Timestamp } from './Timestamp';
import {
  AttentionAlert,
  FreshnessText,
  HealthStateBadge,
  MetaText,
  formatSignalLimits,
  healthStateVisual,
  severityVisual
} from './common';
import { headlineFacts } from './headlineFacts';

/** Headline remains the server assessment, visibly bounded by its coverage. */
export function HealthSummaryPanel({
  summary,
  signals,
  anomalies = null,
  onSelect,
  now
}: Readonly<{
  summary: MachineHealthSummary;
  signals: MachineSignal[] | null;
  anomalies?: MachineAnomaly[] | null;
  onSelect?: (binding: number, trigger: HTMLElement) => void;
  now?: number;
}>) {
  const fact = headlineFacts(summary, signals, anomalies);
  const { coverage, critical } = fact;
  const counts = summary.anomaly_counts;
  const activeCounts = counts
    ? (['critical', 'warning', 'info'] as const).filter(
        (severity) => counts[severity] > 0
      )
    : null;
  const coverageReason = [
    ...fact.criticalMissing.map(
      ({ signal, reason }) => `${signal.display_name}: ${reason}`
    ),
    !critical
      ? t`No critical-signal designation is supplied. Critical limits do not designate critical signals.`
      : null,
    t`Coverage is limited to engineer-designated bindings. Unassessed or missing evidence remains unknown; coverage does not establish equipment health.`
  ]
    .filter(Boolean)
    .join(' ');
  const newest = signals?.find(
    (s) => s.observed_at === summary.last_observed_at
  );
  const assessedText = coverage
    ? t`Assessed on ${coverage.evaluated} of ${coverage.mapped} signals`
    : t`Signal coverage unavailable: signal read failed`;
  const criticalText = critical
    ? critical.evaluated < critical.designated
      ? t`Critical signals: ${critical.evaluated} of ${critical.designated} assessed`
      : t`Critical signals: ${critical.designated} assessed`
    : t`Critical signals: not designated`;
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
    <Stack gap={4} data-testid='health-summary'>
      <Stack gap={6}>
        <Group gap='sm' align='center' wrap='nowrap' style={{ maxWidth: 640 }}>
          <ValueTooltip
            label={`${assessedText}. ${criticalText}. ${coverageReason}`}
            accessibleLabel={`${healthStateVisual(summary.state).label} · ${assessedText}. ${criticalText}. ${coverageReason}`}
          >
            <HealthStateBadge
              state={summary.state}
              size='lg'
              neutralNormal={fact.coverageIncomplete}
              coverage={
                coverage
                  ? { evaluated: coverage.evaluated, mapped: coverage.mapped }
                  : null
              }
            />
          </ValueTooltip>
          {signals === null && (
            <FailedValue
              reason={t`Signal coverage unavailable: signal read failed`}
            />
          )}
        </Group>
        {fact.noObservation && (
          <Text size='sm'>
            {plural(coverage!.mapped, {
              one: 'No observation from # mapped signal',
              other: 'No observation from # mapped signals'
            })}
          </Text>
        )}
        {fact.why === 'signals' && (
          <Stack component='section' gap={2} aria-label={t`Assessment cause`}>
            {fact.matchingSignals.map((s) => (
              <Text size='sm' key={s.binding_id}>
                {signalLink(s)} <SignalValue signal={s} /> · {conditionText(s)}{' '}
                ({formatSignalLimits(s) ?? t`Not configured`}) · {t`observed`}{' '}
                <Timestamp
                  value={s.observed_at}
                  label={t`Observed`}
                  focusable={false}
                />
              </Text>
            ))}
          </Stack>
        )}
        {fact.why === 'anomaly' && (
          <Text size='sm'>{t`From active anomaly: ${fact.anomaly!.title}`}</Text>
        )}
        {fact.why === 'unavailable' && (
          <Group gap={6}>
            <MetaText size='xs'>{t`Assessment cause`}:</MetaText>
            <MissingValue
              reason={t`Assessment cause not supplied by the summary`}
            />
          </Group>
        )}
        {counts && activeCounts && activeCounts.length > 0 && (
          <Group gap={6}>
            {activeCounts.map((severity) => (
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
                {severityVisual(severity).label} · {counts[severity]}
              </StatusBadge>
            ))}
          </Group>
        )}
        <Group gap='xs'>
          {!counts ? (
            <Group gap={6}>
              <MetaText size='xs'>{t`Active anomalies`}:</MetaText>
              <MissingValue reason={t`Active anomaly counts unavailable`} />
            </Group>
          ) : anomalies !== null &&
            summary.active_anomaly_count === 0 &&
            activeCounts?.length === 0 ? (
            <MetaText size='xs'>{t`0 active anomalies`}</MetaText>
          ) : null}
          <FreshnessText
            now={now}
            preferJustNow={recordedTelemetryAsLive(summary)}
            observedAt={summary.last_observed_at}
            stale={newest?.stale ?? false}
            thresholdSeconds={newest?.freshness_threshold_seconds}
            compactOnPhone
            label={t`Last observation`}
          />
        </Group>
      </Stack>
      <Stack gap={4}>
        {summary.state === 'offline' && (
          <MetaText>
            {recordedTelemetryAsLive(summary)
              ? t`Every mapped signal is older than its source's freshness budget. Current readings are unavailable.`
              : t`Every mapped signal is older than its source's freshness budget. Values are historical, not the machine's current state.`}
          </MetaText>
        )}
        {summary.degraded_data && summary.state !== 'offline' && (
          <AttentionAlert
            title={t`Degraded data`}
          >{t`Some signals are stale or reported with poor quality. Confirm the readings you rely on before acting on them.`}</AttentionAlert>
        )}
      </Stack>
    </Stack>
  );
}
export default HealthSummaryPanel;
