import { t } from '@lingui/core/macro';
import { Box, Group, Paper, Stack, Table, Text } from '@mantine/core';
import { displayInstant } from '../displayClock';
import { SourcePollStatus } from './SourcePollStatus';
import { MetaText } from './common';

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
      <Paper withBorder p='md'>
        <MetaText>{t`No health source is configured for this machine.`}</MetaText>
      </Paper>
    );
  }

  return (
    <Paper
      withBorder
      p={0}
      style={{ overflowX: 'auto' }}
      component='section'
      aria-label={t`Source connections; scroll for all columns`}
      tabIndex={0}
    >
      <Table striped miw={720} aria-label={t`Source connections`}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Source`}</Table.Th>
            <Table.Th>{t`Last poll`}</Table.Th>
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
                  <Box w='max-content' style={{ flexShrink: 0 }}>
                    <SourceTypeBadge type={source.source_type} />
                  </Box>
                  <Text size='sm'>{source.name}</Text>
                </Group>
              </Table.Td>
              <Table.Td>
                <SourcePollStatus source={source} />
              </Table.Td>
              <Table.Td>
                <Text size='sm'>
                  {source.last_success_at
                    ? (displayInstant(source.last_success_at) ??
                      t`Time unavailable`)
                    : t`Not recorded`}
                </Text>
              </Table.Td>
              <Table.Td>
                <Stack gap={0}>
                  <Text size='sm'>
                    {source.last_error_at
                      ? (displayInstant(source.last_error_at) ??
                        t`Time unavailable`)
                      : t`Not recorded`}
                  </Text>
                  {source.last_error_code && (
                    <MetaText size='xs'>{source.last_error_code}</MetaText>
                  )}
                </Stack>
              </Table.Td>
              <Table.Td>
                <Text size='sm'>
                  {t`${source.freshness_threshold_seconds} s`}
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
