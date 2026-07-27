import { t } from '@lingui/core/macro';
import { Alert, Badge, Card, Group, Stack, Table, Text } from '@mantine/core';
import { IconCircleCheck, IconLock, IconShieldLock } from '@tabler/icons-react';

import type { RepairGate } from '@lib/types/WorkOrderOverview';

const SATISFIED_STATUSES = new Set(['confirmed', 'verified', 'waived']);

function gateStatusColor(status: string): string {
  if (status === 'waived') {
    return 'orange';
  }
  return SATISFIED_STATUSES.has(status) ? 'green' : 'red';
}

/**
 * Safety gates and what they currently block.
 *
 * Read-only on purpose. Gates are confirmed, verified and waived through their
 * own governed actions with their own evidence requirements; a summary page must
 * not become a second, easier place to mark safety satisfied.
 *
 * A waived gate is shown as waived rather than folded into "satisfied": somebody
 * took responsibility for skipping it, and that should stay visible.
 */
export function WorkOrderSafetyReadiness({
  gates
}: Readonly<{ gates: RepairGate[] }>) {
  const blocking = gates.filter(
    (gate) => gate.is_blocking && !SATISFIED_STATUSES.has(gate.status)
  );
  const waived = gates.filter((gate) => gate.status === 'waived');

  return (
    <Card withBorder padding='md'>
      <Stack gap='md'>
        <Group justify='space-between' align='center'>
          <Group gap='xs'>
            <IconShieldLock size={18} />
            <Text fw={600}>{t`Safety and readiness`}</Text>
          </Group>
          {blocking.length === 0 ? (
            <Badge
              color='green'
              variant='light'
              leftSection={<IconCircleCheck size={14} />}
            >
              {t`No blocking gates`}
            </Badge>
          ) : (
            <Badge
              color='red'
              variant='light'
              leftSection={<IconLock size={14} />}
            >
              {t`${blocking.length} blocking gate(s)`}
            </Badge>
          )}
        </Group>

        {blocking.length > 0 && (
          <Alert color='red' variant='light'>
            {t`This repair cannot start until these gates are satisfied through the safety flow.`}
          </Alert>
        )}

        {waived.length > 0 && (
          <Alert color='orange' variant='light'>
            {t`${waived.length} gate(s) were waived. A waiver is a recorded decision, not a satisfied requirement.`}
          </Alert>
        )}

        {gates.length === 0 ? (
          <Text c='dimmed' size='sm'>
            {t`No safety gates are attached to this repair.`}
          </Text>
        ) : (
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t`Gate`}</Table.Th>
                <Table.Th>{t`Type`}</Table.Th>
                <Table.Th>{t`Status`}</Table.Th>
                <Table.Th>{t`Blocking`}</Table.Th>
                <Table.Th>{t`Evidence required`}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {gates.map((gate) => (
                <Table.Tr key={gate.id}>
                  <Table.Td>
                    <Text size='sm'>{gate.name}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant='outline' color='gray'>
                      {gate.gate_type}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Badge color={gateStatusColor(gate.status)} variant='light'>
                      {gate.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm'>
                      {gate.is_blocking ? t`Blocking` : t`Advisory`}
                      {gate.is_mandatory ? ` · ${t`mandatory`}` : ''}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm' c='dimmed'>
                      {[
                        gate.requires_photo ? t`photo` : null,
                        gate.requires_second_person ? t`second person` : null
                      ]
                        .filter(Boolean)
                        .join(', ') || '—'}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Card>
  );
}

export default WorkOrderSafetyReadiness;
