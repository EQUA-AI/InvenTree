import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Badge,
  Button,
  Group,
  Stack,
  Table,
  Text
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconEdit, IconPlus } from '@tabler/icons-react';
import { useState } from 'react';

import type {
  LockoutPoint,
  RepairPacket,
  RepairPacketGate
} from '@lib/types/Repair';

import { LockoutPointModal } from './LockoutPointModal';
import { lockoutStatusColor } from './safetyApi';

/** Nested table of LOTO lockout points for a gate, with create/edit actions. */
export function LockoutPointTable({
  packet,
  gate,
  onRefresh
}: Readonly<{
  packet: RepairPacket;
  gate: RepairPacketGate;
  onRefresh: () => void;
}>) {
  const [opened, { open, close }] = useDisclosure(false);
  const [editing, setEditing] = useState<LockoutPoint | null>(null);

  const points = gate.lockout_points ?? [];

  const openCreate = () => {
    setEditing(null);
    open();
  };

  const openEdit = (point: LockoutPoint) => {
    setEditing(point);
    open();
  };

  return (
    <Stack gap='xs'>
      <Group justify='space-between'>
        <Text size='sm' fw={600}>
          {t`Lockout points`}
        </Text>
        <Button
          size='xs'
          variant='light'
          leftSection={<IconPlus size={14} />}
          onClick={openCreate}
        >
          {t`Add lockout point`}
        </Button>
      </Group>

      {points.length === 0 ? (
        <Text size='sm' c='dimmed'>
          {t`No lockout points yet.`}
        </Text>
      ) : (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t`Energy`}</Table.Th>
              <Table.Th>{t`Device`}</Table.Th>
              <Table.Th>{t`Lock`}</Table.Th>
              <Table.Th>{t`Tag`}</Table.Th>
              <Table.Th>{t`Status`}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {points.map((point) => (
              <Table.Tr key={point.pk}>
                <Table.Td>{point.energy_source}</Table.Td>
                <Table.Td>{point.isolation_device}</Table.Td>
                <Table.Td>{point.lock_id}</Table.Td>
                <Table.Td>{point.tag_id}</Table.Td>
                <Table.Td>
                  <Badge
                    color={lockoutStatusColor(point.status)}
                    variant='light'
                  >
                    {point.status}
                  </Badge>
                </Table.Td>
                <Table.Td>
                  <ActionIcon
                    variant='subtle'
                    aria-label={t`Edit lockout point`}
                    onClick={() => openEdit(point)}
                  >
                    <IconEdit size={16} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <LockoutPointModal
        packet={packet}
        gate={gate}
        point={editing}
        opened={opened}
        onClose={close}
        onRefresh={onRefresh}
      />
    </Stack>
  );
}
