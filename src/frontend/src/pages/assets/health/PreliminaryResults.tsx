import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Card,
  Group,
  List,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconCircleX,
  IconHelpCircle
} from '@tabler/icons-react';
import dayjs from 'dayjs';

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
 */
export function PreliminaryResultsPanel({
  results
}: Readonly<{ results: PreliminaryResults }>) {
  const status = statusVisual(results.status);
  const provisional = !results.verified_by_user;

  return (
    <Card withBorder radius='md' p='md'>
      <Stack gap='sm'>
        <Group justify='space-between' align='center' wrap='wrap'>
          <Group gap='xs'>
            <Text fw={600}>
              {provisional ? t`Preliminary results` : t`Diagnosis`}
            </Text>
            <Badge color={status.color} variant='light'>
              {status.label}
            </Badge>
            {results.status === 'available' && (
              <Tooltip
                label={t`Confidence reflects only how usable the data was, not how well the failure is understood.`}
              >
                <Badge variant='outline' color='gray'>
                  {t`Confidence ${Math.round(results.confidence * 100)}%`}
                </Badge>
              </Tooltip>
            )}
          </Group>
          {results.generated_at && (
            <Text size='xs' c='dimmed'>
              {t`Generated ${dayjs(results.generated_at).format('MMM D, HH:mm')} by ${results.provider}`}
            </Text>
          )}
        </Group>

        {provisional && (
          <Alert
            color='yellow'
            variant='light'
            icon={<IconAlertTriangle size={16} />}
          >
            {t`Preliminary — not technician verified. This restates what the telemetry shows; it is not a confirmed cause and it authorizes nothing.`}
          </Alert>
        )}

        <Text size='sm'>{results.likely_cause}</Text>

        {results.evidence.length > 0 && (
          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Evidence`}
            </Text>
            {results.evidence.map((item) => {
              const relation = relationVisual(item.relation);
              return (
                <Group
                  key={item.snapshot_id ?? item.observation}
                  gap='xs'
                  wrap='nowrap'
                  align='flex-start'
                >
                  <Badge
                    size='sm'
                    color={relation.color}
                    variant='light'
                    leftSection={relation.icon}
                  >
                    {relation.label}
                  </Badge>
                  <Stack gap={0} style={{ flex: 1 }}>
                    <Text size='sm'>{item.observation}</Text>
                    {item.snapshot_id && (
                      <Text size='xs' c='dimmed'>
                        {t`Snapshot ${item.snapshot_id.slice(0, 8)}`}
                        {item.stale ? ` · ${t`stale at capture`}` : ''}
                      </Text>
                    )}
                  </Stack>
                </Group>
              );
            })}
          </Stack>
        )}

        {results.alternatives.length > 0 && (
          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Rule these out first`}
            </Text>
            <List size='sm' spacing={2}>
              {results.alternatives.map((item) => (
                <List.Item key={item}>{item}</List.Item>
              ))}
            </List>
          </Stack>
        )}

        {results.confirm_tests.length > 0 && (
          <Stack gap={4}>
            <Text size='xs' c='dimmed' fw={500}>
              {t`Suggested confirmation tests`}
            </Text>
            <List size='sm' spacing={2}>
              {results.confirm_tests.map((item) => (
                <List.Item key={item}>{item}</List.Item>
              ))}
            </List>
          </Stack>
        )}

        <Group gap='lg' wrap='wrap'>
          <Text size='xs' c='dimmed'>
            {t`Based on ${results.data_window.snapshot_count} snapshot(s)`}
          </Text>
          {results.freshness.stale && (
            <Text size='xs' c='dimmed'>
              {t`${results.freshness.stale_signal_count} stale signal(s)`}
            </Text>
          )}
          {results.quality.bad_signal_count > 0 && (
            <Text size='xs' c='dimmed'>
              {t`${results.quality.bad_signal_count} signal(s) of poor quality`}
            </Text>
          )}
        </Group>
      </Stack>
    </Card>
  );
}

export default PreliminaryResultsPanel;
