import { t } from '@lingui/core/macro';
import { Alert, Anchor, Badge, Group, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconUrgent } from '@tabler/icons-react';
import dayjs from 'dayjs';
import { Link } from 'react-router-dom';

import type { WorkOrderSourceAlert } from '@lib/types/WorkOrderOverview';

/**
 * The source alert this work order answers.
 *
 * Placed at the top because it is usually why the page was opened. The detector
 * and its version are shown so the reader knows what raised the condition: a
 * threshold rule and a control-system alarm carry different weight, and neither
 * is an AI judgement.
 *
 * The external alert id and alarm code are kept as human cross-references. They
 * are never used as internal keys - the link below uses the machine's id.
 */
export function WorkOrderAlertContext({
  alert
}: Readonly<{ alert: WorkOrderSourceAlert }>) {
  const critical = alert.severity === 'critical';

  return (
    <Alert
      color={critical ? 'red' : 'yellow'}
      variant='light'
      icon={
        critical ? <IconUrgent size={16} /> : <IconAlertTriangle size={16} />
      }
      title={t`Raised from a health alert`}
    >
      <Stack gap='xs'>
        <Group gap='xs' wrap='wrap'>
          <Text fw={600}>{alert.title}</Text>
          <Badge color={critical ? 'red' : 'yellow'} variant='light'>
            {alert.severity}
          </Badge>
          <Badge variant='outline' color='gray'>
            {alert.status}
          </Badge>
          {alert.alarm_code && (
            <Badge variant='outline' color='gray'>
              {alert.alarm_code}
            </Badge>
          )}
        </Group>

        {alert.evidence_summary && (
          <Text size='sm'>{alert.evidence_summary}</Text>
        )}

        <Group gap='lg' wrap='wrap'>
          <Text size='xs' c='dimmed'>
            {t`First observed ${dayjs(alert.first_observed_at).format('MMM D, HH:mm')}`}
          </Text>
          <Text size='xs' c='dimmed'>
            {t`Last observed ${dayjs(alert.last_observed_at).format('MMM D, HH:mm')}`}
          </Text>
          <Text size='xs' c='dimmed'>
            {t`Detected by ${alert.detector || t`an unnamed rule`}`}
            {alert.detector_version ? ` v${alert.detector_version}` : ''}
          </Text>
          {alert.source_name && (
            <Text size='xs' c='dimmed'>
              {t`Source ${alert.source_name}`}
            </Text>
          )}
          {alert.external_id && (
            <Text size='xs' c='dimmed'>
              {t`External ref ${alert.external_id}`}
            </Text>
          )}
        </Group>

        <Anchor
          component={Link}
          to={`/machines/machine/${alert.machine_id}/health`}
          size='sm'
        >
          {t`Open the machine's Health blade`}
        </Anchor>
      </Stack>
    </Alert>
  );
}

export default WorkOrderAlertContext;
