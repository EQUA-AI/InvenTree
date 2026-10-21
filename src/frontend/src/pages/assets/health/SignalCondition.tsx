import type { MachineAnomaly, MachineSignal } from '@lib/types/MachineHealth';
import { plural, t } from '@lingui/core/macro';
import { Stack, Text } from '@mantine/core';
import { Link } from 'react-router-dom';
import { displayInstant } from '../displayClock';
import { MetaText, MonitoringLink } from './common';
import { unassociatedAnomalyText } from './common';
import { anomaliesForSignal } from './signalContext';

export function conditionText(signal: MachineSignal): string {
  if (signal.state === 'offline')
    return t`No current data; last-known values only`;
  if (!signal.observed_at || signal.value === null)
    return t`Not assessed: no observation`;
  if (signal.stale) return t`Not assessed: stale last-known reading`;
  if (signal.quality !== 'good') return t`Not assessed: quality is not good`;
  switch (signal.state) {
    case 'critical':
      return t`Critical against configured limits`;
    case 'warning':
      return t`Warning against configured limits`;
    case 'normal':
      return t`Normal within this signal's configured limits`;
    default:
      return Object.values(signal.limits).some((value) => value !== null)
        ? t`Not assessed: no current classification`
        : t`Not assessed: limits not configured`;
  }
}

/** One machine-level anomaly read, associated only by canonical binding id. */
export function SignalAnomalyContext({
  signal,
  anomalies
}: Readonly<{
  signal: MachineSignal;
  anomalies: MachineAnomaly[] | null;
}>) {
  if (anomalies === null)
    return (
      <Text size='sm'>{t`Association unavailable: anomaly read incomplete or failed`}</Text>
    );
  const { cited, unassociated } = anomaliesForSignal(
    anomalies,
    signal.binding_id
  );
  return (
    <Stack gap={4}>
      {cited.length > 0 ? (
        <Text size='sm'>
          {plural(cited.length, {
            one: '# active anomaly cites this signal',
            other: '# active anomalies cite this signal'
          })}
        </Text>
      ) : unassociated.length === 0 ? (
        <Text size='sm'>{t`No active anomalies cite this signal`}</Text>
      ) : null}
      {cited.map((anomaly) => (
        <Stack key={anomaly.pk} gap={0}>
          <Text size='sm'>{anomaly.title}</Text>
          <MetaText size='xs'>
            {anomaly.first_observed_at
              ? (displayInstant(anomaly.first_observed_at) ??
                t`Time unavailable`)
              : t`Occurrence time not recorded`}
          </MetaText>
          {anomaly.work_order ? (
            <MonitoringLink
              component={Link}
              size='sm'
              to={`/maintenance/work-orders/${anomaly.work_order}/`}
            >{t`Open owning work ${anomaly.work_order}`}</MonitoringLink>
          ) : (
            <MetaText size='xs'>{t`No owning work recorded`}</MetaText>
          )}
        </Stack>
      ))}
      {unassociated.length > 0 && (
        <Text size='sm'>{unassociatedAnomalyText(unassociated.length)}</Text>
      )}
    </Stack>
  );
}
