import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  TextInput,
  Textarea
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useEffect, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  LockoutPoint,
  RepairPacket,
  RepairPacketGate
} from '@lib/types/Repair';

import { useApi } from '../../../../contexts/ApiContext';
import { showApiErrorMessage } from '../../../../functions/notifications';
import { energySourceOptions, lockoutStatusOptions } from './safetyApi';

/**
 * Create or update a LOTO energy-control point. Restoration and zero-energy
 * verification are hazardous, deliberate steps — the modal calls them out.
 */
export function LockoutPointModal({
  packet,
  gate,
  point,
  opened,
  onClose,
  onRefresh
}: Readonly<{
  packet: RepairPacket;
  gate: RepairPacketGate;
  point?: LockoutPoint | null;
  opened: boolean;
  onClose: () => void;
  onRefresh: () => void;
}>) {
  const api = useApi();
  const [energySource, setEnergySource] = useState('electrical');
  const [isolationDevice, setIsolationDevice] = useState('');
  const [lockId, setLockId] = useState('');
  const [tagId, setTagId] = useState('');
  const [status, setStatus] = useState('identified');
  const [note, setNote] = useState('');
  const [loading, setLoading] = useState(false);

  // Sync form state to the point being edited whenever the modal opens.
  useEffect(() => {
    if (!opened) {
      return;
    }
    setEnergySource(point?.energy_source ?? 'electrical');
    setIsolationDevice(point?.isolation_device ?? '');
    setLockId(point?.lock_id ?? '');
    setTagId(point?.tag_id ?? '');
    setStatus(point?.status ?? 'identified');
    setNote(point?.note ?? '');
  }, [opened, point]);

  const submit = async () => {
    setLoading(true);
    try {
      await api.post(
        apiUrl(ApiEndpoints.repair_packet_gate_lockout, packet.pk, {
          gate_pk: gate.pk
        }),
        {
          pk: point?.pk,
          energy_source: energySource,
          isolation_device: isolationDevice,
          lock_id: lockId,
          tag_id: tagId,
          status,
          note
        }
      );
      notifications.show({ color: 'green', message: t`Lockout point saved` });
      onRefresh();
      onClose();
    } catch (error) {
      showApiErrorMessage({ error, title: t`Failed to save lockout point` });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={point ? t`Edit lockout point` : t`Add lockout point`}
    >
      <Stack>
        <Select
          label={t`Energy source`}
          data={energySourceOptions()}
          value={energySource}
          onChange={(value) => setEnergySource(value ?? 'electrical')}
          allowDeselect={false}
        />
        <TextInput
          label={t`Isolation device`}
          placeholder={t`Breaker / valve / blind id`}
          value={isolationDevice}
          onChange={(event) => setIsolationDevice(event.currentTarget.value)}
        />
        <Group grow>
          <TextInput
            label={t`Lock id`}
            value={lockId}
            onChange={(event) => setLockId(event.currentTarget.value)}
          />
          <TextInput
            label={t`Tag id`}
            value={tagId}
            onChange={(event) => setTagId(event.currentTarget.value)}
          />
        </Group>
        <Select
          label={t`Status`}
          data={lockoutStatusOptions()}
          value={status}
          onChange={(value) => setStatus(value ?? 'identified')}
          allowDeselect={false}
        />
        {status === 'verified' && energySource === 'electrical' ? (
          <Alert color='blue'>
            {t`Capture a zero-energy reading proof (e.g. 0 V L-L / L-G) for this verification.`}
          </Alert>
        ) : null}
        {status === 'restored' ? (
          <Alert color='orange' icon={<IconAlertTriangle />}>
            {t`Restoring energy is a hazardous action. Ensure people, tools and guards are clear before restoring.`}
          </Alert>
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
          <Button loading={loading} onClick={submit}>
            {t`Save`}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
