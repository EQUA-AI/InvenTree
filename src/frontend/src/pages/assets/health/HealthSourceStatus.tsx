import { t } from '@lingui/core/macro';
import { Badge, Group, Paper, Stack, Table, Text } from '@mantine/core';
import { IconCircleCheck, IconPlugConnectedX } from '@tabler/icons-react';
import dayjs from 'dayjs';

import type { HealthSourceStatus } from '@lib/types/MachineHealth';

import { SourceTypeBadge } from './common';

/**
 * Connection health for the sources mapped to this machine.
 *
 * The error column shows a redacted classification, never a provider message:
 * connector errors routinely carry endpoints, tag names and occasionally
 * credentials, none of which belong on an operator's screen.
 */
export function HealthSourceStatusTable({
  sources
}: Readonly<{ sources: HealthSourceStatus[] }>) {
  if (sources.length === 0) {
    return (
      <Paper withBorder radius='md' p='md'>
        <Text c='dimmed'>{t`No health source is configured for this machine.`}</Text>
      </Paper>
    );
  }

  return (
    <Paper withBorder radius='md' p={0} style={{ overflowX: 'auto' }}>
      <Table striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Source`}</Table.Th>
            <Table.Th>{t`Connection`}</Table.Th>
            <Table.Th>{t`Last success`}</Table.Th>
            <Table.Th>{t`Last error`}</Table.Th>
            <Table.Th>{t`Freshness budget`}</Table.Th>
            <Table.Th>{t`Mapped tags`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {sources.map((source) => (
            <Table.Tr key={source.source_id}>
              <Table.Td>
                <Group gap={6} wrap='nowrap'>
                  <SourceTypeBadge type={source.source_type} />
                  <Text size='sm'>{source.name}</Text>
                </Group>
              </Table.Td>
              <Table.Td>
                {source.healthy ? (
                  <Badge
                    color='green'
                    variant='light'
                    leftSection={<IconCircleCheck size={14} />}
                  >
                    {t`Connected`}
                  </Badge>
                ) : (
                  <Badge
                    color='red'
                    variant='light'
                    leftSection={<IconPlugConnectedX size={14} />}
                  >
                    {source.active ? t`Failing` : t`Disabled`}
                  </Badge>
                )}
              </Table.Td>
              <Table.Td>
                <Text size='sm'>
                  {source.last_success_at
                    ? dayjs(source.last_success_at).format('MMM D, HH:mm')
                    : t`Never`}
                </Text>
              </Table.Td>
              <Table.Td>
                <Stack gap={0}>
                  <Text size='sm'>
                    {source.last_error_at
                      ? dayjs(source.last_error_at).format('MMM D, HH:mm')
                      : '—'}
                  </Text>
                  {source.last_error_code && (
                    <Text size='xs' c='dimmed'>
                      {source.last_error_code}
                    </Text>
                  )}
                </Stack>
              </Table.Td>
              <Table.Td>
                <Text size='sm'>
                  {t`${Math.round(source.freshness_threshold_seconds / 60)} min`}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size='sm'>{source.mapped_tag_count}</Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Paper>
  );
}

export default HealthSourceStatusTable;
