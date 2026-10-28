import { t } from '@lingui/core/macro';
import { Alert, Badge, Card, Group, Stack, Table, Text } from '@mantine/core';
import { IconCircleCheck, IconLock, IconShieldLock } from '@tabler/icons-react';
import { useEffect, useState } from 'react';
import { WorkTableScroll } from '../../../../components/tables/WorkTableScroll';

import type { AuthorityRecord } from '@lib/types/OperationalAuthority';
import type { RepairGate } from '@lib/types/WorkOrderOverview';
import { gateTypeLabel } from '../../../repair/components/safety/safetyApi';
import { WarningText } from '../../../tasks/workorders/execution/ExecutionFacts';

const SATISFIED_STATUSES = new Set(['confirmed', 'verified', 'waived']);

/** Translated gate status; an unrecognised value is named as such, not shown raw. */
function gateStatusText(status: string): string {
  return (
    (
      {
        pending: t`Pending`,
        confirmed: t`Confirmed`,
        verified: t`Verified`,
        waived: t`Waived`
      } as Record<string, string>
    )[status] ?? t`Status not recognized`
  );
}

function gateStatusColor(gate: RepairGate, expired: boolean): string {
  const status = gate.status;
  if (gate.is_blocking && (gate.is_satisfied === false || expired))
    return 'red';
  if (status === 'waived') {
    return 'orange';
  }
  return SATISFIED_STATUSES.has(status)
    ? gate.is_satisfied === true
      ? 'green'
      : 'gray'
    : 'red';
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
  gates,
  physical
}: Readonly<{ gates: RepairGate[]; physical?: AuthorityRecord[] }>) {
  const { expired } = useGateAuthorityClock(gates, physical);
  const blockingReason = (gate: RepairGate) =>
    expired(gate) && gate.is_satisfied !== false
      ? t`Recorded authority expired. Refresh current gate checks.`
      : gate.unsatisfied_reason;
  const blocking = gates.filter(
    (gate) =>
      gate.is_blocking &&
      (gate.is_satisfied === false ||
        expired(gate) ||
        !SATISFIED_STATUSES.has(gate.status))
  );
  const unknown = gates.filter(
    (gate) =>
      gate.is_blocking &&
      typeof gate.is_satisfied !== 'boolean' &&
      SATISFIED_STATUSES.has(gate.status)
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
          {gates.length === 0 ||
          (blocking.length === 0 && unknown.length > 0) ? (
            <Badge
              color='gray'
              variant='light'
            >{t`Gate readiness not evaluated`}</Badge>
          ) : blocking.length === 0 ? (
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
            <Stack gap='xs'>
              <Text size='sm'>{t`This repair cannot start until these gates are satisfied through the safety flow.`}</Text>
              {blocking.map((gate) =>
                blockingReason(gate) ? (
                  <Text
                    key={gate.id}
                    size='sm'
                    style={{ overflowWrap: 'anywhere' }}
                  >
                    {gate.name}: {blockingReason(gate)}
                  </Text>
                ) : null
              )}
            </Stack>
          </Alert>
        )}

        {unknown.length > 0 && (
          <Alert
            role='note'
            color='gray'
            variant='light'
          >{t`Current gate checks are not supplied. A recorded gate status alone does not establish physical authority.`}</Alert>
        )}

        {waived.length > 0 && (
          <Alert role='note' color='orange' variant='light'>
            {t`${waived.length} gate(s) were waived. A waiver is a recorded decision, not a satisfied requirement.`}
          </Alert>
        )}

        {gates.length === 0 ? (
          <Stack gap='xs'>
            <Text c='dimmed' size='sm'>
              {t`No safety gates are attached to this repair.`}
            </Text>
            <Text size='sm'>
              {t`The absence of attached gates does not establish site safety authorization or permission to start physical work.`}
            </Text>
          </Stack>
        ) : (
          <WorkTableScroll
            minWidth={720}
            label={t`Safety gates table, scroll for all columns`}
          >
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
                        {gateTypeLabel(gate.gate_type)}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Badge
                        color={gateStatusColor(gate, expired(gate))}
                        variant='light'
                      >
                        {gateStatusText(gate.status)}
                      </Badge>
                      {(gate.is_satisfied === false || expired(gate)) &&
                        blockingReason(gate) && (
                          <WarningText size='xs'>
                            {blockingReason(gate)}
                          </WarningText>
                        )}
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
          </WorkTableScroll>
        )}
      </Stack>
    </Card>
  );
}

export default WorkOrderSafetyReadiness;

function useGateAuthorityClock(
  gates: RepairGate[],
  physical?: AuthorityRecord[]
) {
  const [now, setNow] = useState(Date.now);
  // A saved evaluation can lose readiness as time passes. Only remove a
  // positive claim here; becoming effective still requires a fresh native check.
  useEffect(() => {
    const expiry = Math.min(
      ...(physical ?? [])
        .filter(
          (row) => row.current && gates.some((gate) => gate.id === row.gate_id)
        )
        .map((row) => Date.parse(row.effective_until ?? ''))
        .filter((time) => Number.isFinite(time) && time > now)
    );
    const refreshClock = () => setNow(Date.now());
    const timer = Number.isFinite(expiry)
      ? window.setTimeout(
          refreshClock,
          Math.min(2_147_483_647, Math.max(0, expiry - Date.now()))
        )
      : undefined;
    document.addEventListener('visibilitychange', refreshClock);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', refreshClock);
    };
  }, [physical, gates, now]);
  const expired = (gate: RepairGate) =>
    (physical ?? []).some(
      (row) =>
        row.current &&
        row.gate_id === gate.id &&
        row.effective_until &&
        Date.parse(row.effective_until) <= now
    );

  return { expired };
}
