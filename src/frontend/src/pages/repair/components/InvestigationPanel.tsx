import { t } from '@lingui/core/macro';
import {
  Badge,
  Card,
  Group,
  List,
  Paper,
  Stack,
  Table,
  Text,
  Tooltip
} from '@mantine/core';
import {
  IconCircleCheck,
  IconHelpCircle,
  IconRuler
} from '@tabler/icons-react';
import dayjs from 'dayjs';

import type {
  ApprovedRepairScope,
  FindingCategory,
  FindingVerification,
  RepairInvestigationFinding
} from '@lib/types/Repair';

const CATEGORY_LABELS: Record<FindingCategory, () => string> = {
  telemetry: () => t`Telemetry`,
  measurement: () => t`Measurement`,
  inspection: () => t`Inspection`,
  operator: () => t`Operator report`,
  test: () => t`Functional test`,
  other: () => t`Other`
};

function verificationVisual(verification: FindingVerification) {
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
 * Investigation findings and the scope that was approved.
 *
 * Findings are shown as rows rather than folded into the description so a reader
 * can tell a SCADA reading from a technician's measurement, see its unit, and
 * see whether anyone has checked it. Unverified is stated plainly - an
 * unconfirmed observation should not read like an established fact.
 *
 * The approved scope is a frozen version. Later AI regeneration changes the
 * preliminary content but cannot rewrite what an approver signed off, and the
 * version number here is what makes that visible.
 */
export function InvestigationPanel({
  findings,
  approvedScope
}: Readonly<{
  findings: RepairInvestigationFinding[];
  approvedScope: ApprovedRepairScope | null;
}>) {
  return (
    <Stack gap='lg'>
      <Stack gap='sm'>
        <Text fw={600}>{t`Investigation findings`}</Text>
        {findings.length === 0 ? (
          <Paper withBorder radius='md' p='md'>
            <Text c='dimmed'>{t`No findings have been recorded yet.`}</Text>
          </Paper>
        ) : (
          <Paper withBorder radius='md' p={0} style={{ overflowX: 'auto' }}>
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
                    <Table.Tr key={finding.pk}>
                      <Table.Td>
                        <Text size='sm' fw={500}>
                          {finding.finding_key}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        <Badge variant='light' color='gray'>
                          {CATEGORY_LABELS[finding.category]?.() ??
                            finding.category}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Text size='sm'>{finding.observation}</Text>
                      </Table.Td>
                      <Table.Td>
                        {finding.value === null ? (
                          <Text size='sm' c='dimmed'>
                            —
                          </Text>
                        ) : (
                          <Group gap={4} wrap='nowrap'>
                            <IconRuler size={14} />
                            <Text size='sm'>
                              {finding.value}
                              {finding.unit ? ` ${finding.unit}` : ''}
                            </Text>
                          </Group>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Stack gap={0}>
                          <Text size='sm'>
                            {finding.evidence_source || '—'}
                          </Text>
                          {finding.snapshot && (
                            <Tooltip
                              label={t`Cites an immutable health evidence snapshot`}
                            >
                              <Text size='xs' c='dimmed'>
                                {t`Snapshot ${finding.snapshot.slice(0, 8)}`}
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
          </Paper>
        )}
      </Stack>

      <Stack gap='sm'>
        <Text fw={600}>{t`Approved repair scope`}</Text>
        {approvedScope === null ? (
          <Paper withBorder radius='md' p='md'>
            <Text c='dimmed'>{t`No scope has been approved yet.`}</Text>
          </Paper>
        ) : (
          <Card withBorder radius='md' p='md'>
            <Stack gap='sm'>
              <Group gap='xs'>
                <Badge variant='light'>
                  {t`Version ${approvedScope.version}`}
                </Badge>
                {approvedScope.approved_by_name && (
                  <Text size='sm' c='dimmed'>
                    {t`Approved by ${approvedScope.approved_by_name} on ${dayjs(approvedScope.approved_at).format('MMM D, YYYY')}`}
                  </Text>
                )}
              </Group>

              {approvedScope.verified_cause && (
                <Stack gap={0}>
                  <Text size='xs' c='dimmed' fw={500}>
                    {t`Verified cause`}
                  </Text>
                  <Text size='sm'>{approvedScope.verified_cause}</Text>
                </Stack>
              )}

              <Stack gap={4}>
                <Text size='xs' c='dimmed' fw={500}>
                  {t`Scope`}
                </Text>
                <List type='ordered' size='sm' spacing={2}>
                  {approvedScope.scope_lines.map((line) => (
                    <List.Item key={line.sequence}>{line.action}</List.Item>
                  ))}
                </List>
              </Stack>

              <Group gap='lg' wrap='wrap'>
                {approvedScope.crew_size !== null && (
                  <Text size='sm'>{t`Crew: ${approvedScope.crew_size}`}</Text>
                )}
                {approvedScope.planned_elapsed_minutes !== null && (
                  <Text size='sm'>
                    {t`Planned elapsed: ${approvedScope.planned_elapsed_minutes} min`}
                  </Text>
                )}
                {approvedScope.failure_codes.length > 0 && (
                  <Group gap={4}>
                    {approvedScope.failure_codes.map((code) => (
                      <Badge key={code} variant='outline' color='gray'>
                        {code}
                      </Badge>
                    ))}
                  </Group>
                )}
              </Group>
            </Stack>
          </Card>
        )}
      </Stack>
    </Stack>
  );
}

export default InvestigationPanel;
