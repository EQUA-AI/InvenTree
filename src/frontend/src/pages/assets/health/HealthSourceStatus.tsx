import { t } from '@lingui/core/macro';
import { Box, Group, Paper, Table, Text } from '@mantine/core';
import { MissingValue } from '../../../components/items/ValueTooltip';
import { formatDuration } from '../performance/format';
import { recordedTelemetryAsLive } from '../telemetryPresentation';
import { SourcePollStatus } from './SourcePollStatus';
import { Timestamp } from './Timestamp';
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
            <Table.Th>{t`Status`}</Table.Th>
            <Table.Th>
              {sources.some(
                (source) =>
                  recordedTelemetryAsLive(source) && source.reporting != null
              )
                ? t`Last reading`
                : t`Last success`}
            </Table.Th>
            <Table.Th>{t`Last error`}</Table.Th>
            <Table.Th>{t`Stale after`}</Table.Th>
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
                <Timestamp
                  value={
                    recordedTelemetryAsLive(source) && source.reporting != null
                      ? source.last_observed_at
                      : source.last_success_at
                  }
                />
              </Table.Td>
              <Table.Td>
                {source.last_error_at ? (
                  <Timestamp
                    value={source.last_error_at}
                    label={t`Last error`}
                  />
                ) : (
                  <MissingValue reason={t`No source error recorded`} />
                )}
              </Table.Td>
              <Table.Td>
                <Text size='sm'>
                  {formatDuration(source.freshness_threshold_seconds)}
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
