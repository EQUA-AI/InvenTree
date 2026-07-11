import { t } from '@lingui/core/macro';
import {
  Button,
  Group,
  JsonInput,
  Modal,
  NumberInput,
  Select,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { RepairPacket, RepairPacketGate } from '@lib/types/Repair';

import { useApi } from '../../../../contexts/ApiContext';
import { showApiErrorMessage } from '../../../../functions/notifications';
import { proofTypeOptions } from './safetyApi';

/**
 * Attach a structured proof record to a gate. Provides friendly inputs for the
 * common `scan` and `reading` shapes and a raw JSON fallback for the rest.
 *
 * Note: this records proof *metadata*. Binary photo files still use the packet
 * attachment system; a `photo` proof here references that evidence.
 */
export function SafetyGateProofModal({
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
  const [proofType, setProofType] = useState('reading');
  const [lockoutPoint, setLockoutPoint] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // scan fields
  const [scanCode, setScanCode] = useState('');
  const [scanLabel, setScanLabel] = useState('');

  // reading fields
  const [readingType, setReadingType] = useState('');
  const [readingValue, setReadingValue] = useState<number | string>('');
  const [readingUnit, setReadingUnit] = useState('');
  const [readingPhase, setReadingPhase] = useState('');

  // generic fallback
  const [rawValue, setRawValue] = useState('{}');

  const lockoutOptions = useMemo(
    () =>
      (gate.lockout_points ?? []).map((point) => ({
        value: String(point.pk),
        label: `${point.energy_source} — ${point.isolation_device || t`(no device)`}`
      })),
    [gate.lockout_points]
  );

  const buildValue = (): Record<string, unknown> | null => {
    if (proofType === 'scan') {
      return { code: scanCode, label: scanLabel, source: 'manual' };
    }
    if (proofType === 'reading') {
      return {
        reading_type: readingType,
        value: readingValue,
        unit: readingUnit,
        phase: readingPhase
      };
    }
    try {
      return JSON.parse(rawValue || '{}');
    } catch {
      return null;
    }
  };

  const submit = async () => {
    const value = buildValue();
    if (value === null) {
      notifications.show({
        color: 'red',
        title: t`Invalid proof value`,
        message: t`The proof value must be valid JSON.`
      });
      return;
    }

    setLoading(true);
    try {
      await api.post(
        apiUrl(ApiEndpoints.repair_packet_gate_proof, packet.pk, {
          gate_pk: gate.pk
        }),
        {
          proof_type: proofType,
          lockout_point: lockoutPoint ? Number(lockoutPoint) : null,
          value
        }
      );
      notifications.show({ color: 'green', message: t`Proof recorded` });
      onRefresh();
      onClose();
    } catch (error) {
      showApiErrorMessage({ error, title: t`Failed to record proof` });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={t`Add proof`}>
      <Stack>
        <Text fw={600}>{gate.name}</Text>
        <Select
          label={t`Proof type`}
          data={proofTypeOptions()}
          value={proofType}
          onChange={(value) => setProofType(value ?? 'reading')}
          allowDeselect={false}
        />
        {lockoutOptions.length > 0 ? (
          <Select
            label={t`Lockout point`}
            placeholder={t`Not linked to a lockout point`}
            data={lockoutOptions}
            value={lockoutPoint}
            onChange={setLockoutPoint}
            clearable
          />
        ) : null}

        {proofType === 'scan' ? (
          <>
            <TextInput
              label={t`Scanned code`}
              value={scanCode}
              onChange={(event) => setScanCode(event.currentTarget.value)}
            />
            <TextInput
              label={t`Label`}
              value={scanLabel}
              onChange={(event) => setScanLabel(event.currentTarget.value)}
            />
          </>
        ) : null}

        {proofType === 'reading' ? (
          <>
            <TextInput
              label={t`Reading type`}
              placeholder='voltage'
              value={readingType}
              onChange={(event) => setReadingType(event.currentTarget.value)}
            />
            <Group grow>
              <NumberInput
                label={t`Value`}
                value={readingValue}
                onChange={setReadingValue}
              />
              <TextInput
                label={t`Unit`}
                placeholder='V'
                value={readingUnit}
                onChange={(event) => setReadingUnit(event.currentTarget.value)}
              />
              <TextInput
                label={t`Phase`}
                placeholder='L-L'
                value={readingPhase}
                onChange={(event) => setReadingPhase(event.currentTarget.value)}
              />
            </Group>
          </>
        ) : null}

        {proofType !== 'scan' && proofType !== 'reading' ? (
          <JsonInput
            label={t`Value`}
            value={rawValue}
            onChange={setRawValue}
            autosize
            minRows={3}
            formatOnBlur
          />
        ) : null}

        <Group justify='flex-end'>
          <Button variant='default' onClick={onClose}>
            {t`Cancel`}
          </Button>
          <Button loading={loading} onClick={submit}>
            {t`Add proof`}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
