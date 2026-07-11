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
import { IconInfoCircle } from '@tabler/icons-react';
import { useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import type { RepairPacket, RepairPacketGate } from '@lib/types/Repair';

import { useApi } from '../../../../contexts/ApiContext';
import { postGateAction } from './safetyApi';

/**
 * Independent second-person verification for gates that require it. The backend
 * rejects verification by the same user who confirmed the gate.
 */
export function SafetyGateVerifyModal({
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
      ApiEndpoints.repair_packet_gate_verify,
      packet.pk,
      gate.pk,
      { note }
    );
    setLoading(false);

    if (result.ok) {
      notifications.show({ color: 'green', message: t`Gate verified` });
      onRefresh();
      onClose();
    } else {
      notifications.show({
        color: 'red',
        title: t`Cannot verify gate`,
        message: result.detail
      });
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={t`Verify safety gate`}>
      <Stack>
        <Text fw={600}>{gate.name}</Text>
        <Alert color='blue' icon={<IconInfoCircle />}>
          {t`The verifier must be a different person from the one who confirmed the gate.`}
        </Alert>
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
            {t`Verify`}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
