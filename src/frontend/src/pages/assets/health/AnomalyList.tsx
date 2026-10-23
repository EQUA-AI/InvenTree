import { t } from '@lingui/core/macro';
import {
  Button,
  Card,
  Group,
  Paper,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import {
  IconBellOff,
  IconCheck,
  IconExternalLink,
  IconStethoscope,
  IconTool
} from '@tabler/icons-react';
import { Link } from 'react-router-dom';
import { displayInstant } from '../displayClock';
import monitoring from '../monitoring.module.css';
import { StatusBadge, alarmStateVisual } from './StatusBadge';
import { MetaText } from './common';

import { UserRoles } from '@lib/enums/Roles';
import { useUserState } from '../../../states/UserState';

import type {
  MachineAnomaly,
  PreliminaryResults
} from '@lib/types/MachineHealth';

import { AnomalyTimeShift } from './AnomalyTimeShift';
import { PreliminaryResultsPanel } from './PreliminaryResults';
import { SeverityBadge, SourceTypeBadge } from './common';

/**
 * Active anomalies for one machine.
 *
 * Each card shows what was detected, by which rule and over what window, so an
 * operator can judge the finding rather than take it on faith. Acknowledging is
 * offered as a distinct, weaker action than raising a repair: it records that
 * somebody looked, and changes nothing about the machine.
 */
export function AnomalyList({
  anomalies,
  onAcknowledge,
  onCreateRepair,
  onAnalyze,
  onDismiss,
  acknowledging,
  analyzing,
  results
}: Readonly<{
  anomalies: MachineAnomaly[];
  onAcknowledge: (anomaly: MachineAnomaly) => void;
  onCreateRepair: (anomaly: MachineAnomaly) => void;
  onAnalyze: (anomaly: MachineAnomaly) => void;
  /** Dismiss an alarm as wrong, with a reason; omitted where not offered. */
  onDismiss?: (anomaly: MachineAnomaly) => void;
  acknowledging: number | null;
  analyzing: number | null;
  results: Record<number, PreliminaryResults>;
}>) {
  const user = useUserState();
  const canChange = user.hasChangeRole(UserRoles.work_order);
  const canAdd = user.hasAddRole(UserRoles.work_order);
  if (anomalies.length === 0) {
    return (
      <Paper withBorder p='md'>
        <MetaText>{t`No active anomalies.`}</MetaText>
      </Paper>
    );
  }

  return (
    <Stack gap='sm'>
      {anomalies.map((anomaly) => {
        const visual = alarmStateVisual(anomaly.status, anomaly.severity);
        return (
          <Card key={anomaly.pk} withBorder p='md'>
            <Stack gap='sm'>
              <Group justify='space-between' align='flex-start' wrap='wrap'>
                <Stack gap={4}>
                  <Group gap='xs'>
                    <SeverityBadge severity={anomaly.severity} />
                    <StatusBadge
                      tone='neutral'
                      variant={visual.variant}
                      color={visual.color}
                      leftSection={visual.icon}
                    >
                      {visual.label}
                    </StatusBadge>
                    <SourceTypeBadge type={anomaly.source_type} />
                    {anomaly.alarm_code && (
                      <StatusBadge
                        multiline
                        tone='neutral'
                        variant='outline'
                        color='gray'
                      >
                        {anomaly.alarm_code}
                      </StatusBadge>
                    )}
                  </Group>
                  <Text fw={600}>{anomaly.title}</Text>
                  {anomaly.evidence_summary && (
                    <MetaText size='sm'>{anomaly.evidence_summary}</MetaText>
                  )}
                </Stack>

                <Group gap='xs' wrap='wrap'>
                  {canChange && (
                    <Button
                      className={monitoring.link}
                      size='xs'
                      variant='subtle'
                      leftSection={<IconStethoscope size={14} />}
                      loading={analyzing === anomaly.pk}
                      disabled={!canChange}
                      aria-label={t`Analyze ${anomaly.title}`}
                      onClick={() => onAnalyze(anomaly)}
                    >
                      {t`Analyze`}
                    </Button>
                  )}
                  {canChange && anomaly.status === 'open' && (
                    <Button
                      className={monitoring.link}
                      size='xs'
                      variant='subtle'
                      leftSection={<IconCheck size={14} />}
                      loading={acknowledging === anomaly.pk}
                      disabled={!canChange}
                      aria-label={t`Acknowledge ${anomaly.title}`}
                      onClick={() => onAcknowledge(anomaly)}
                    >
                      {t`Acknowledge`}
                    </Button>
                  )}
                  {anomaly.work_order ? (
                    <Button
                      size='xs'
                      variant='light'
                      component={Link}
                      to={`/maintenance/work-orders/${anomaly.work_order}/`}
                      leftSection={<IconExternalLink size={14} />}
                    >
                      {t`Open work order`}
                    </Button>
                  ) : canAdd ? (
                    <Button
                      size='xs'
                      variant='light'
                      leftSection={<IconTool size={14} />}
                      aria-label={t`Create repair for ${anomaly.title}`}
                      onClick={() => onCreateRepair(anomaly)}
                      disabled={!canAdd}
                    >
                      {t`Create repair`}
                    </Button>
                  ) : null}
                  {canChange && onDismiss && (
                    <Button
                      size='xs'
                      variant='subtle'
                      color='gray'
                      leftSection={<IconBellOff size={14} />}
                      aria-label={t`Dismiss ${anomaly.title}`}
                      onClick={() => onDismiss(anomaly)}
                      disabled={!canChange}
                    >
                      {t`Dismiss`}
                    </Button>
                  )}
                </Group>
              </Group>

              {(!canChange || !canAdd) && (
                <MetaText>{t`View only: acknowledging, dismissing and creating repairs need Work order permissions.`}</MetaText>
              )}
              <Group gap='lg' wrap='wrap'>
                <Stack gap={0}>
                  <MetaText size='xs'>{t`First observed`}</MetaText>
                  <Text size='sm'>
                    {displayInstant(anomaly.first_observed_at) ??
                      t`Time unavailable`}
                  </Text>
                  <AnomalyTimeShift
                    anomaly={anomaly}
                    time={anomaly.first_observed_at}
                  />
                </Stack>
                <Stack gap={0}>
                  <MetaText size='xs'>{t`Last observed`}</MetaText>
                  <Text size='sm'>
                    {displayInstant(anomaly.last_observed_at) ??
                      t`Time unavailable`}
                  </Text>
                  <AnomalyTimeShift
                    anomaly={anomaly}
                    time={anomaly.last_observed_at}
                  />
                </Stack>
                <Stack gap={0}>
                  <MetaText size='xs'>{t`Detected by`}</MetaText>
                  <Tooltip
                    label={t`Detection is deterministic: a source alarm or a configured threshold, never an AI judgement.`}
                  >
                    <Text size='sm'>
                      {anomaly.detector || t`Unknown`}
                      {anomaly.detector_version
                        ? ` v${anomaly.detector_version}`
                        : ''}
                    </Text>
                  </Tooltip>
                </Stack>
                {anomaly.signals.length > 0 && (
                  <Stack gap={0}>
                    <MetaText size='xs'>{t`Signals`}</MetaText>
                    <Text size='sm'>
                      {anomaly.signals
                        .map((signal) => signal.display_name)
                        .join(', ')}
                    </Text>
                  </Stack>
                )}
              </Group>

              {results[anomaly.pk] && (
                <PreliminaryResultsPanel
                  results={results[anomaly.pk]}
                  machineId={anomaly.machine}
                />
              )}

              {anomaly.acknowledged_by_name && (
                <MetaText size='xs'>
                  {t`Acknowledged by ${anomaly.acknowledged_by_name}`}
                  {anomaly.acknowledgement_note
                    ? ` — ${anomaly.acknowledgement_note}`
                    : ''}
                </MetaText>
              )}
            </Stack>
          </Card>
        );
      })}
    </Stack>
  );
}

export default AnomalyList;
