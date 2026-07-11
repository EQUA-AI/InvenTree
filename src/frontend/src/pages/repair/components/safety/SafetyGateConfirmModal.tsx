import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Modal,
  Stack,
  Text,
  Textarea
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import type { RepairPacket, RepairPacketGate } from '@lib/types/Repair';

import { useApi } from '../../../../contexts/ApiContext';
import { postGateAction } from './safetyApi';

/** Confirm a safety gate; surfaces backend blocker reasons on failure. */
export function SafetyGateConfirmModal({
  packet,
  gate,
  opened,
  onClose,
  onRefresh
}: Readonly<{
  packet: RepairPacket;
  gate: RepairPacketGate;
  opened: boolean;
  onClose: () => void;
  onRefresh: () => void;
}>) {
  const api = useApi();
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    setLoading(true);
    const result = await postGateAction(
      api,
      ApiEndpoints.repair_packet_gate_confirm,
      packet.pk,
      gate.pk,
      { note }
    );
    setLoading(false);

    if (result.ok) {
      notifications.show({ color: 'green', message: t`Gate confirmed` });
      onRefresh();
      onClose();
    } else {
      notifications.show({
        color: 'red',
        title: t`Cannot confirm gate`,
        message: result.detail
      });
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={t`Confirm safety gate`}>
      <Stack>
        <Text fw={600}>{gate.name}</Text>
        {gate.unsatisfied_reason ? (
          <Alert color='orange' icon={<IconAlertTriangle />}>
            {t`Outstanding requirement`}: {gate.unsatisfied_reason}
          </Alert>
        ) : null}
        {gate.requires_photo ? (
          <Text size='sm' c='dimmed'>
            {t`This gate requires photo proof before it can be confirmed.`}
          </Text>
        ) : null}
        <Textarea
          label={t`Note`}
          value={note}
          onChange={(event) => setNote(event.currentTarget.value)}
          autosize
          minRows={2}
        />
        <Group justify='flex-end'>
          <Button variant='default' onClick={onClose}>
            {t`Cancel`}
          </Button>
          <Button color='green' loading={loading} onClick={submit}>
            {t`Confirm`}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
