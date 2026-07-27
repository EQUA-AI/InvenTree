import { t } from '@lingui/core/macro';
import {
  Badge,
  Card,
  Group,
  List,
  Stack,
  Table,
  Text,
  Tooltip
} from '@mantine/core';
import { IconCircleCheck, IconHelpCircle } from '@tabler/icons-react';
import dayjs from 'dayjs';

import type {
  OverviewApprovedScope,
  OverviewFinding
} from '@lib/types/WorkOrderOverview';

function verificationVisual(verification: string) {
  switch (verification) {
    case 'verified':
      return {
        color: 'green',
        label: t`Verified`,
        icon: <IconCircleCheck size={14} />
      };
    case 'disputed':
      return {
        color: 'orange',
        label: t`Disputed`,
        icon: <IconHelpCircle size={14} />
      };
    default:
      return {
        color: 'gray',
        label: t`Unverified`,
        icon: <IconHelpCircle size={14} />
      };
  }
}

/**
 * Investigation findings and the approved repair scope.
 *
 * Findings are rows so a reader can distinguish a control-system reading from a
 * technician's measurement, see its unit and see whether anyone has checked it.
 * Unverified is stated rather than implied.
 *
 * The scope shown is the version that was approved. Later regeneration produces
 * new preliminary content but cannot rewrite it, and the version number is what
 * makes that visible on the page.
 */
export function InvestigationSection({
  findings,
  approvedScope
}: Readonly<{
  findings: OverviewFinding[];
  approvedScope: OverviewApprovedScope | null;
}>) {
  if (findings.length === 0 && approvedScope === null) {
    return null;
  }

  return (
    <Stack gap='md'>
      {findings.length > 0 && (
        <Card withBorder padding='md'>
          <Stack gap='sm'>
            <Text fw={600}>{t`Investigation findings`}</Text>
            <Table striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t`Finding`}</Table.Th>
                  <Table.Th>{t`Category`}</Table.Th>
                  <Table.Th>{t`Observation`}</Table.Th>
                  <Table.Th>{t`Value`}</Table.Th>
                  <Table.Th>{t`Source`}</Table.Th>
                  <Table.Th>{t`Observed`}</Table.Th>
                  <Table.Th>{t`Verification`}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {findings.map((finding) => {
                  const verification = verificationVisual(finding.verification);
                  return (
                    <Table.Tr key={finding.id}>
                      <Table.Td>
                        <Text size='sm' fw={500}>
                          {finding.finding_key}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge variant='light' color='gray'>
                          {finding.category}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size='sm'>{finding.observation}</Text>
                      </Table.Td>
                      <Table.Td>
                        <Text size='sm'>
                          {finding.value === null
                            ? '—'
                            : `${finding.value}${finding.unit ? ` ${finding.unit}` : ''}`}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Stack gap={0}>
                          <Text size='sm'>
                            {finding.evidence_source || '—'}
                          </Text>
                          {finding.snapshot_id && (
                            <Tooltip
                              label={t`Cites an immutable health evidence snapshot`}
                            >
                              <Text size='xs' c='dimmed'>
                                {t`Snapshot ${finding.snapshot_id.slice(0, 8)}`}
                              </Text>
                            </Tooltip>
                          )}
                        </Stack>
                      </Table.Td>
                      <Table.Td>
                        <Text size='sm'>
                          {finding.observed_at
                            ? dayjs(finding.observed_at).format('MMM D, HH:mm')
                            : '—'}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge
                          size='sm'
                          color={verification.color}
                          variant='light'
                          leftSection={verification.icon}
                        >
                          {verification.label}
                        </Badge>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Stack>
        </Card>
      )}

      {approvedScope && (
        <Card withBorder padding='md'>
          <Stack gap='sm'>
            <Group gap='xs'>
              <Text fw={600}>{t`Approved repair scope`}</Text>
              <Badge variant='light'>
                {t`Version ${approvedScope.version}`}
              </Badge>
              <Text size='xs' c='dimmed'>
                {t`Approved ${dayjs(approvedScope.approved_at).format('MMM D, YYYY')}`}
              </Text>
            </Group>

            {approvedScope.verified_cause && (
              <Stack gap={0}>
                <Text size='xs' c='dimmed' fw={500}>
                  {t`Verified cause`}
                </Text>
                <Text size='sm'>{approvedScope.verified_cause}</Text>
              </Stack>
            )}

            <List type='ordered' size='sm' spacing={2}>
              {approvedScope.scope_lines.map((line) => (
                <List.Item key={line.sequence}>{line.action}</List.Item>
              ))}
            </List>

            <Group gap='lg' wrap='wrap'>
              {approvedScope.crew_size !== null && (
                <Text size='sm'>{t`Crew: ${approvedScope.crew_size}`}</Text>
              )}
              {approvedScope.planned_elapsed_minutes !== null && (
                <Text size='sm'>
                  {t`Planned elapsed: ${approvedScope.planned_elapsed_minutes} min`}
                </Text>
              )}
              {approvedScope.failure_codes.map((code) => (
                <Badge key={code} variant='outline' color='gray'>
                  {code}
                </Badge>
              ))}
            </Group>
          </Stack>
        </Card>
      )}
    </Stack>
  );
}

export default InvestigationSection;
