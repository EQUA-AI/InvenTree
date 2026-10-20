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
import { StatusBadge as Badge, alarmStateVisual } from './StatusBadge';

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
        <Text className={monitoring.meta}>{t`No active anomalies.`}</Text>
      </Paper>
    );
  }

  return (
    <Stack gap='sm'>
      {anomalies.map((anomaly) => (
        <Card key={anomaly.pk} withBorder p='md'>
          <Stack gap='sm'>
            <Group justify='space-between' align='flex-start' wrap='wrap'>
              <Stack gap={4}>
                <Group gap='xs'>
                  <SeverityBadge severity={anomaly.severity} />
                  <Badge
                    variant={
                      alarmStateVisual(anomaly.status, anomaly.severity).variant
                    }
                    color={
                      alarmStateVisual(anomaly.status, anomaly.severity).color
                    }
                    leftSection={
                      alarmStateVisual(anomaly.status, anomaly.severity).icon
                    }
                  >
                    {alarmStateVisual(anomaly.status, anomaly.severity).label}
                  </Badge>
                  <SourceTypeBadge type={anomaly.source_type} />
                  {anomaly.alarm_code && (
                    <Badge variant='outline' color='gray'>
                      {anomaly.alarm_code}
                    </Badge>
                  )}
                </Group>
                <Text fw={600}>{anomaly.title}</Text>
                {anomaly.evidence_summary && (
                  <Text size='sm' className={monitoring.meta}>
                    {anomaly.evidence_summary}
                  </Text>
                )}
              </Stack>

              <Group gap='xs' wrap='wrap'>
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
                {anomaly.status === 'open' && (
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
                ) : (
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
                )}
                {onDismiss && (
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

            <Group gap='lg' wrap='wrap'>
              <Stack gap={0}>
                <Text
                  size='xs'
                  className={monitoring.meta}
                >{t`First observed`}</Text>
                <Text size='sm'>
                  {displayInstant(anomaly.first_observed_at) ??
                    t`Time unavailable`}
                </Text>
              </Stack>
              <Stack gap={0}>
                <Text
                  size='xs'
                  className={monitoring.meta}
                >{t`Last observed`}</Text>
                <Text size='sm'>
                  {displayInstant(anomaly.last_observed_at) ??
                    t`Time unavailable`}
                </Text>
              </Stack>
              <Stack gap={0}>
                <Text
                  size='xs'
                  className={monitoring.meta}
                >{t`Detected by`}</Text>
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
                  <Text
                    size='xs'
                    className={monitoring.meta}
                  >{t`Signals`}</Text>
                  <Text size='sm'>
                    {anomaly.signals
                      .map((signal) => signal.display_name)
                      .join(', ')}
                  </Text>
                </Stack>
              )}
            </Group>

            <AnomalyTimeShift anomaly={anomaly} />

            {results[anomaly.pk] && (
              <PreliminaryResultsPanel
                results={results[anomaly.pk]}
                machineId={anomaly.machine}
              />
            )}

            {anomaly.acknowledged_by_name && (
              <Text size='xs' className={monitoring.meta}>
                {t`Acknowledged by ${anomaly.acknowledged_by_name}`}
                {anomaly.acknowledgement_note
                  ? ` — ${anomaly.acknowledgement_note}`
                  : ''}
              </Text>
            )}
          </Stack>
        </Card>
      ))}
    </Stack>
  );
}

export default AnomalyList;
