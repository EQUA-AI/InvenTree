import { t } from '@lingui/core/macro';
import {
  Badge,
  Button,
  Card,
  Group,
  Stack,
  Text,
  ThemeIcon
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconCamera,
  IconCircleCheck,
  IconFlame,
  IconShieldLock
} from '@tabler/icons-react';

import type { RepairPacket, RepairPacketGate } from '@lib/types/Repair';

import { LockoutPointTable } from './LockoutPointTable';
import { SafetyGateConfirmModal } from './SafetyGateConfirmModal';
import { SafetyGateProofModal } from './SafetyGateProofModal';
import { SafetyGateStatusBadge } from './SafetyGateStatusBadge';
import { SafetyGateVerifyModal } from './SafetyGateVerifyModal';
import { SafetyGateWaiveModal } from './SafetyGateWaiveModal';
import { gateSupportsLockout, gateTypeLabel } from './safetyApi';

/** Left-border accent colour reflecting the gate's blocking/satisfied state. */
function accentColor(gate: RepairPacketGate): string {
  if (gate.status === 'waived') {
    return 'orange';
  }
  if (gate.unsatisfied_reason) {
    return 'red';
  }
  if (gate.status === 'confirmed') {
    return 'green';
  }
  return 'yellow';
}

function GateIcon({ gateType }: Readonly<{ gateType: string }>) {
  if (gateType === 'hot_work') {
    return <IconFlame size={18} />;
  }
  return <IconShieldLock size={18} />;
}

/**
 * A single safety gate: status, requirements, blocker reason, actions, and
 * (for LOTO/isolation) the nested lockout-point workflow.
 */
export function SafetyGateCard({
  packet,
  gate,
  onRefresh
}: Readonly<{
  packet: RepairPacket;
  gate: RepairPacketGate;
  onRefresh: () => void;
}>) {
  const [confirmOpened, confirmHandlers] = useDisclosure(false);
  const [verifyOpened, verifyHandlers] = useDisclosure(false);
  const [waiveOpened, waiveHandlers] = useDisclosure(false);
  const [proofOpened, proofHandlers] = useDisclosure(false);

  const accent = accentColor(gate);
  const proofCount = gate.proofs?.length ?? 0;

  return (
    <Card
      withBorder
      radius='sm'
      p='md'
      style={{ borderLeft: `4px solid var(--mantine-color-${accent}-6)` }}
    >
      <Stack gap='sm'>
        <Group justify='space-between' align='flex-start' wrap='nowrap'>
          <Group gap='xs' wrap='nowrap'>
            <ThemeIcon color={accent} variant='light'>
              <GateIcon gateType={gate.gate_type} />
            </ThemeIcon>
            <div>
              <Text fw={600}>{gate.name}</Text>
              <Text size='xs' c='dimmed'>
                {gateTypeLabel(gate.gate_type)}
              </Text>
            </div>
          </Group>
          <SafetyGateStatusBadge gate={gate} />
        </Group>

        <Group gap={6}>
          {gate.is_blocking ? (
            <Badge size='sm' color='red' variant='outline'>
              {t`Blocking`}
            </Badge>
          ) : null}
          {gate.is_mandatory ? (
            <Badge size='sm' variant='outline'>
              {t`Mandatory`}
            </Badge>
          ) : (
            <Badge size='sm' color='gray' variant='outline'>
              {t`Advisory`}
            </Badge>
          )}
          {gate.requires_photo ? (
            <Badge size='sm' color='grape' variant='light'>
              {t`Photo required`}
            </Badge>
          ) : null}
          {gate.requires_second_person ? (
            <Badge size='sm' color='indigo' variant='light'>
              {t`Second person`}
            </Badge>
          ) : null}
          {proofCount > 0 ? (
            <Badge
              size='sm'
              color='teal'
              variant='light'
              leftSection={<IconCamera size={12} />}
            >
              {t`${proofCount} proof(s)`}
            </Badge>
          ) : null}
          {gate.verified_by ? (
            <Badge
              size='sm'
              color='green'
              variant='light'
              leftSection={<IconCircleCheck size={12} />}
            >
              {t`Verified`}
            </Badge>
          ) : null}
        </Group>

        {gate.unsatisfied_reason ? (
          <Text size='sm' c='red'>
            {t`Outstanding`}: {gate.unsatisfied_reason}
          </Text>
        ) : null}

        {gate.status === 'waived' && gate.waiver_reason ? (
          <Text size='sm' c='dimmed'>
            {t`Waived`}: {gate.waiver_reason}
            {gate.waiver_authority ? ` (${gate.waiver_authority})` : ''}
          </Text>
        ) : null}

        <Group gap='xs'>
          {gate.status !== 'confirmed' ? (
            <Button size='xs' color='green' onClick={confirmHandlers.open}>
              {t`Confirm`}
            </Button>
          ) : null}
          {gate.requires_second_person && !gate.verified_by ? (
            <Button size='xs' variant='light' onClick={verifyHandlers.open}>
              {t`Verify`}
            </Button>
          ) : null}
          {gate.is_blocking && gate.status !== 'waived' ? (
            <Button
              size='xs'
              variant='light'
              color='orange'
              onClick={waiveHandlers.open}
            >
              {t`Waive`}
            </Button>
          ) : null}
          <Button size='xs' variant='default' onClick={proofHandlers.open}>
            {t`Add proof`}
          </Button>
        </Group>

        {gateSupportsLockout(gate.gate_type) ? (
          <LockoutPointTable
            packet={packet}
            gate={gate}
            onRefresh={onRefresh}
          />
        ) : null}
      </Stack>

      <SafetyGateConfirmModal
        packet={packet}
        gate={gate}
        opened={confirmOpened}
        onClose={confirmHandlers.close}
        onRefresh={onRefresh}
      />
      <SafetyGateVerifyModal
        packet={packet}
        gate={gate}
        opened={verifyOpened}
        onClose={verifyHandlers.close}
        onRefresh={onRefresh}
      />
      <SafetyGateWaiveModal
        packet={packet}
        gate={gate}
        opened={waiveOpened}
        onClose={waiveHandlers.close}
        onRefresh={onRefresh}
      />
      <SafetyGateProofModal
        packet={packet}
        gate={gate}
        opened={proofOpened}
        onClose={proofHandlers.close}
        onRefresh={onRefresh}
      />
    </Card>
  );
}
