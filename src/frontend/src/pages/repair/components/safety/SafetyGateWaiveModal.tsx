import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Modal,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import type { RepairPacket, RepairPacketGate } from '@lib/types/Repair';

import { useApi } from '../../../../contexts/ApiContext';
import { isApprovalRequired, postGateAction } from './safetyApi';

/**
 * Waive a safety gate. A waiver is a controlled, audited exception — never a
 * silent skip. High-risk gates (template `risk_tier >= 3`) are routed through
 * the approvals system; the backend returns an approval-created response which
 * we surface as "routed for approval", not as a failure.
 */
export function SafetyGateWaiveModal({
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
  const [reason, setReason] = useState('');
  const [authority, setAuthority] = useState('');
  const [loading, setLoading] = useState(false);

  const reset = () => {
    setReason('');
    setAuthority('');
  };

  const submit = async () => {
    setLoading(true);
    const result = await postGateAction(
      api,
      ApiEndpoints.repair_packet_gate_waive,
      packet.pk,
      gate.pk,
      { reason, authority }
    );
    setLoading(false);

    if (result.ok) {
      notifications.show({ color: 'orange', message: t`Gate waived` });
      onRefresh();
      reset();
      onClose();
      return;
    }

    if (isApprovalRequired(result)) {
      notifications.show({
        color: 'blue',
        title: t`Waiver routed for approval`,
        message: t`This is a high-risk gate. The waiver requires an approved request before it takes effect.`
      });
      onRefresh();
      reset();
      onClose();
      return;
    }

    notifications.show({
      color: 'red',
      title: t`Cannot waive gate`,
      message: result.detail
    });
  };

  const disabled = reason.trim() === '' || authority.trim() === '';

  return (
    <Modal opened={opened} onClose={onClose} title={t`Waive safety gate`}>
      <Stack>
        <Text fw={600}>{gate.name}</Text>
        {gate.is_mandatory ? (
          <Alert color='red' icon={<IconAlertTriangle />}>
            {t`This is a mandatory, blocking gate. Waiving it is an audited exception and may require supervisor approval.`}
          </Alert>
        ) : null}
        <Textarea
          label={t`Reason`}
          required
          value={reason}
          onChange={(event) => setReason(event.currentTarget.value)}
          autosize
          minRows={2}
        />
        <TextInput
          label={t`Authorising person`}
          required
          value={authority}
          onChange={(event) => setAuthority(event.currentTarget.value)}
        />
        <Group justify='flex-end'>
          <Button variant='default' onClick={onClose}>
            {t`Cancel`}
          </Button>
          <Button
            color='orange'
            loading={loading}
            disabled={disabled}
            onClick={submit}
          >
            {t`Waive`}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
