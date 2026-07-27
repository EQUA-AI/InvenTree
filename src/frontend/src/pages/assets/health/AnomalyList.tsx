import { t } from '@lingui/core/macro';
import {
  Badge,
  Button,
  Card,
  Group,
  Paper,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import {
  IconCheck,
  IconExternalLink,
  IconStethoscope,
  IconTool
} from '@tabler/icons-react';
import dayjs from 'dayjs';
import { Link } from 'react-router-dom';

import type {
  MachineAnomaly,
  PreliminaryResults
} from '@lib/types/MachineHealth';

import { PreliminaryResultsPanel } from './PreliminaryResults';
import { SeverityBadge, SourceTypeBadge } from './common';

function statusLabel(status: MachineAnomaly['status']): string {
  switch (status) {
    case 'open':
      return t`Open`;
    case 'acknowledged':
      return t`Acknowledged`;
    case 'resolved':
      return t`Resolved`;
    default:
      return t`Suppressed`;
  }
}

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
  acknowledging,
  analyzing,
  results
}: Readonly<{
  anomalies: MachineAnomaly[];
  onAcknowledge: (anomaly: MachineAnomaly) => void;
  onCreateRepair: (anomaly: MachineAnomaly) => void;
  onAnalyze: (anomaly: MachineAnomaly) => void;
  acknowledging: number | null;
  analyzing: number | null;
  results: Record<number, PreliminaryResults>;
}>) {
  if (anomalies.length === 0) {
    return (
      <Paper withBorder radius='md' p='md'>
        <Text c='dimmed'>{t`No active anomalies.`}</Text>
      </Paper>
    );
  }

  return (
    <Stack gap='sm'>
      {anomalies.map((anomaly) => (
        <Card key={anomaly.pk} withBorder radius='md' p='md'>
          <Stack gap='sm'>
            <Group justify='space-between' align='flex-start' wrap='nowrap'>
              <Stack gap={4}>
                <Group gap='xs'>
                  <SeverityBadge severity={anomaly.severity} />
                  <Badge variant='outline' color='gray'>
                    {statusLabel(anomaly.status)}
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
                  <Text size='sm' c='dimmed'>
                    {anomaly.evidence_summary}
                  </Text>
                )}
              </Stack>

              <Group gap='xs' wrap='nowrap'>
                <Button
                  size='xs'
                  variant='subtle'
                  leftSection={<IconStethoscope size={14} />}
                  loading={analyzing === anomaly.pk}
                  onClick={() => onAnalyze(anomaly)}
                >
                  {t`Analyze`}
                </Button>
                {anomaly.status === 'open' && (
                  <Button
                    size='xs'
                    variant='subtle'
                    leftSection={<IconCheck size={14} />}
                    loading={acknowledging === anomaly.pk}
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
                    onClick={() => onCreateRepair(anomaly)}
                  >
                    {t`Create repair`}
                  </Button>
                )}
              </Group>
            </Group>

            <Group gap='lg' wrap='wrap'>
              <Stack gap={0}>
                <Text size='xs' c='dimmed'>{t`First observed`}</Text>
                <Text size='sm'>
                  {dayjs(anomaly.first_observed_at).format('MMM D, HH:mm')}
                </Text>
              </Stack>
              <Stack gap={0}>
                <Text size='xs' c='dimmed'>{t`Last observed`}</Text>
                <Text size='sm'>
                  {dayjs(anomaly.last_observed_at).format('MMM D, HH:mm')}
                </Text>
              </Stack>
              <Stack gap={0}>
                <Text size='xs' c='dimmed'>{t`Detected by`}</Text>
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
                  <Text size='xs' c='dimmed'>{t`Signals`}</Text>
                  <Text size='sm'>
                    {anomaly.signals
                      .map((signal) => signal.display_name)
                      .join(', ')}
                  </Text>
                </Stack>
              )}
            </Group>

            {results[anomaly.pk] && (
              <PreliminaryResultsPanel results={results[anomaly.pk]} />
            )}

            {anomaly.acknowledged_by_name && (
              <Text size='xs' c='dimmed'>
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
