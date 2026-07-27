import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Modal,
  NumberInput,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';

import { useApi } from '../../../contexts/ApiContext';
import { showApiErrorMessage } from '../../../functions/notifications';

interface CloseoutFormValues {
  cause: string;
  action: string;
  result: string;
  verificationSummary: string;
  downtimeMinutes: number | string;
  followUpRequired: boolean;
  followUp: string;
}

export interface PacketCloseoutModalProps {
  opened: boolean;
  onClose: () => void;
  packetId: number;
  /** Optimistic-concurrency token echoed back to the close command. */
  workOrderVersion: number | null;
  workOrderReference: string | null;
  onClosed?: () => void;
}

/**
 * Return-to-service closeout for a packet that owns a work order.
 *
 * Closing such a packet is not a bare status change: the same request writes the
 * structured work-order closeout, moves the work order to its terminal state and
 * creates the machine's maintenance-history row, all in one transaction. The
 * server refuses the old bare `advance to closed` path for these packets, so this
 * form is the only way through.
 */
export function PacketCloseoutModal({
  opened,
  onClose,
  packetId,
  workOrderVersion,
  workOrderReference,
  onClosed
}: Readonly<PacketCloseoutModalProps>) {
  const api = useApi();
  const isSmallScreen = useMediaQuery('(max-width: 48em)');
  const [saving, setSaving] = useState(false);

  const form = useForm<CloseoutFormValues>({
    initialValues: {
      cause: '',
      action: '',
      result: '',
      verificationSummary: '',
      downtimeMinutes: '',
      followUpRequired: false,
      followUp: ''
    },
    validate: {
      action: (value) =>
        value.trim().length === 0 ? t`Describe what was done.` : null,
      result: (value) =>
        value.trim().length === 0 ? t`Describe the outcome.` : null,
      verificationSummary: (value) =>
        value.trim().length === 0
          ? t`Record how the repair was verified.`
          : null
    }
  });

  const handleClose = () => {
    form.reset();
    setSaving(false);
    onClose();
  };

  const handleSubmit = form.onSubmit(async (values) => {
    setSaving(true);

    try {
      await api.post(apiUrl(ApiEndpoints.repair_packet_close, packetId), {
        expected_version: workOrderVersion,
        closeout: {
          cause: values.cause.trim(),
          action: values.action.trim(),
          result: values.result.trim(),
          verification_summary: values.verificationSummary.trim(),
          downtime_minutes:
            typeof values.downtimeMinutes === 'number'
              ? values.downtimeMinutes
              : null,
          follow_up_required: values.followUpRequired,
          follow_up: values.followUp.trim()
        }
      });
      onClosed?.();
      handleClose();
    } catch (error) {
      showApiErrorMessage({ error, title: t`Could not close the repair` });
      setSaving(false);
    }
  });

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title={t`Close repair and return to service`}
      size='lg'
      fullScreen={isSmallScreen}
    >
      <form onSubmit={handleSubmit}>
        <Stack gap='md'>
          <Alert
            variant='light'
            color='blue'
            icon={<IconAlertTriangle size={16} />}
          >
            <Text size='sm'>
              {workOrderReference
                ? t`This completes work order ${workOrderReference} and writes one permanent record to the machine's maintenance history.`
                : t`This completes the linked work order and writes one permanent record to the machine's maintenance history.`}
            </Text>
          </Alert>

          <TextInput
            label={t`Verified cause`}
            placeholder={t`What actually failed?`}
            {...form.getInputProps('cause')}
          />
          <Textarea
            label={t`Action taken`}
            placeholder={t`What was repaired or replaced?`}
            minRows={2}
            withAsterisk
            {...form.getInputProps('action')}
          />
          <Textarea
            label={t`Result`}
            placeholder={t`What is the equipment doing now?`}
            minRows={2}
            withAsterisk
            {...form.getInputProps('result')}
          />
          <Textarea
            label={t`Verification`}
            placeholder={t`How was the repair proven — readings, run time, acceptance checks`}
            minRows={2}
            withAsterisk
            {...form.getInputProps('verificationSummary')}
          />
          <NumberInput
            label={t`Downtime (minutes)`}
            placeholder={t`Total equipment downtime`}
            min={0}
            {...form.getInputProps('downtimeMinutes')}
          />
          <Switch
            label={t`Follow-up work is required`}
            {...form.getInputProps('followUpRequired', { type: 'checkbox' })}
          />
          {form.values.followUpRequired && (
            <Textarea
              label={t`Follow-up`}
              placeholder={t`What still needs doing?`}
              minRows={2}
              {...form.getInputProps('followUp')}
            />
          )}

          <Group justify='flex-end'>
            <Button variant='default' type='button' onClick={handleClose}>
              {t`Cancel`}
            </Button>
            <Button type='submit' loading={saving}>
              {t`Close repair`}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}

export default PacketCloseoutModal;
