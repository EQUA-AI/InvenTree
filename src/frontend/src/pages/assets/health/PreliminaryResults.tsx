import { plural, t } from '@lingui/core/macro';
import { Alert, Card, Group, List, Stack, Text, Tooltip } from '@mantine/core';
import {
  IconAlertTriangle,
  IconBroadcast,
  IconCircleCheck,
  IconCircleX,
  IconHelpCircle
} from '@tabler/icons-react';
import { Link } from 'react-router-dom';
import { displayInstant } from '../displayClock';
import { StatusBadge } from './StatusBadge';
import { MetaText, MonitoringLink } from './common';

import type {
  AnalysisStatus,
  EvidenceRelation,
  PreliminaryResults
} from '@lib/types/MachineHealth';

function statusVisual(status: AnalysisStatus) {
  switch (status) {
    case 'available':
      return { color: 'blue', label: t`Evidence available` };
    case 'stale':
      return { color: 'yellow', label: t`Data is stale` };
    case 'insufficient':
      return { color: 'yellow', label: t`Data is insufficient` };
    default:
      return { color: 'gray', label: t`No data available` };
  }
}

function relationVisual(relation: EvidenceRelation) {
  switch (relation) {
    case 'supports':
      return {
        color: 'green',
        label: t`Supports`,
        icon: <IconCircleCheck size={14} />
      };
    case 'contradicts':
      return {
        color: 'red',
        label: t`Contradicts`,
        icon: <IconCircleX size={14} />
      };
    default:
      return {
        color: 'gray',
        label: t`Unclear`,
        icon: <IconHelpCircle size={14} />
      };
  }
}

/**
 * Preliminary results for one anomaly.
 *
 * Deliberately never titled "Diagnosis". Until a technician verifies it, this is
 * a restatement of measurements with citations attached, and the component says
 * so in the banner rather than in fine print. Each observation shows the
 * snapshot it came from and whether it supports the stated cause, so nobody has
 * to take the conclusion on trust; where the data was missing or stale, that is
 * shown as the finding rather than hidden.
 *
 * One thing here is not hedged: when the source system declared the alarm
 * itself, the boundaries it was raised against are configured in that hub, and
 * whether the machine is outside them is that system's call. The panel says so
 * plainly instead of presenting it as our inference - while still labelling the
 * *cause* preliminary, because that part is ours and remains unverified.
 */
export function PreliminaryResultsPanel({
  results,
  machineId
}: Readonly<{ results: PreliminaryResults; machineId?: number }>) {
  const status = statusVisual(results.status);
  const provisional = !results.verified_by_user;
  // Named rather than a boolean so the source name narrows to a string wherever
  // it is interpolated below.
  const declaredBy =
    results.authority === 'source_declared' ? results.authority_source : null;

  return (
    <Card withBorder p='md'>
      <Stack gap='sm'>
        <Group justify='space-between' align='center' wrap='wrap'>
          <Group gap='xs'>
            <Text fw={600}>
              {provisional ? t`Preliminary results` : t`Diagnosis`}
            </Text>
            <StatusBadge tone='neutral' color={status.color} variant='outline'>
              {status.label}
            </StatusBadge>
            {declaredBy && (
              <Tooltip
                label={t`${declaredBy} raised this alarm against limits configured in that system. Whether the machine is outside them is its determination, not an inference made here.`}
              >
                <StatusBadge
                  tone='neutral'
                  color='blue'
                  variant='outline'
                  leftSection={<IconBroadcast size={14} />}
                >
                  {t`Declared by ${declaredBy}`}
                </StatusBadge>
              </Tooltip>
            )}
            {(results.status === 'available' || !!declaredBy) && (
              <Tooltip
                label={
                  declaredBy
                    ? t`Confidence reflects the source system's own declaration and how usable the supporting data was - not how well the failure is understood.`
                    : t`Confidence reflects only how usable the data was, not how well the failure is understood.`
                }
              >
                <StatusBadge tone='neutral' variant='outline' color='gray'>
                  {t`Confidence ${Math.round(results.confidence * 100)}%`}
                </StatusBadge>
              </Tooltip>
            )}
          </Group>
          {results.generated_at && (
            <MetaText size='xs'>
              {t`Generated ${displayInstant(results.generated_at) ?? t`Time unavailable`} by ${results.provider}`}
            </MetaText>
          )}
        </Group>

        {provisional && (
          <Alert
            color='yellow'
            variant='light'
            icon={<IconAlertTriangle size={16} />}
          >
            {declaredBy
              ? t`Preliminary — not technician verified. The alarm is ${declaredBy}'s own determination, but the cause below is not confirmed and it authorizes nothing.`
              : t`Preliminary — not technician verified. This restates what the telemetry shows; it is not a confirmed cause and it authorizes nothing.`}
          </Alert>
        )}

        <Text size='sm'>{results.likely_cause}</Text>

        {results.evidence.length > 0 && (
          <Stack gap={4}>
            <MetaText size='xs' fw={500}>
              {t`Evidence`}
            </MetaText>
            {results.evidence.map((item) => {
              const relation = relationVisual(item.relation);
              return (
                <Group
                  key={item.snapshot_id ?? item.observation}
                  gap='xs'
                  wrap='nowrap'
                  align='flex-start'
                >
                  <StatusBadge
                    tone='neutral'
                    size='sm'
                    color={relation.color}
                    variant='outline'
                    leftSection={relation.icon}
                  >
                    {relation.label}
                  </StatusBadge>
                  <Stack gap={0} style={{ flex: 1 }}>
                    <Text size='sm'>{item.observation}</Text>
                    {item.snapshot_id && (
                      <MetaText size='xs'>
                        {machineId ? (
                          <MonitoringLink
                            component={Link}
                            size='xs'
                            to={`/machines/machine/${machineId}/health/?snapshot=${encodeURIComponent(item.snapshot_id)}`}
                          >
                            {t`Snapshot ${item.snapshot_id.slice(0, 8)}`}
                          </MonitoringLink>
                        ) : (
                          t`Snapshot ${item.snapshot_id.slice(0, 8)}`
                        )}
                        {item.stale ? ` · ${t`stale at capture`}` : ''}
                      </MetaText>
                    )}
                  </Stack>
                </Group>
              );
            })}
          </Stack>
        )}

        {results.alternatives.length > 0 && (
          <Stack gap={4}>
            <MetaText size='xs' fw={500}>
              {t`Rule these out first`}
            </MetaText>
            <List size='sm' spacing={2}>
              {results.alternatives.map((item) => (
                <List.Item key={item}>{item}</List.Item>
              ))}
            </List>
          </Stack>
        )}

        {results.confirm_tests.length > 0 && (
          <Stack gap={4}>
            <MetaText size='xs' fw={500}>
              {t`Suggested confirmation tests`}
            </MetaText>
            <List size='sm' spacing={2}>
              {results.confirm_tests.map((item) => (
                <List.Item key={item}>{item}</List.Item>
              ))}
            </List>
          </Stack>
        )}

        <Group gap='lg' wrap='wrap'>
          <MetaText size='xs'>
            {plural(results.data_window.snapshot_count, {
              one: 'Based on # snapshot',
              other: 'Based on # snapshots'
            })}
          </MetaText>
          {results.freshness.stale && (
            <MetaText size='xs'>
              {plural(results.freshness.stale_signal_count, {
                one: '# stale signal',
                other: '# stale signals'
              })}
            </MetaText>
          )}
          {results.quality.bad_signal_count > 0 && (
            <MetaText size='xs'>
              {plural(results.quality.bad_signal_count, {
                one: '# signal of poor quality',
                other: '# signals of poor quality'
              })}
            </MetaText>
          )}
        </Group>
      </Stack>
    </Card>
  );
}

export default PreliminaryResultsPanel;
