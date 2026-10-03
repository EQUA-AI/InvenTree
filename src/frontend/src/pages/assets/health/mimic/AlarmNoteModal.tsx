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
import { useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { api } from '../../../../App';

/** The alarm an action is taken on: which anomaly, on which machine. */
export type AlarmTarget = {
  machine: number;
  anomaly: number;
  /** What the alarm is, in a line, for the person about to act on it. */
  summary: string;
};

/**
 * The two things that can be said about an alarm, each with a note.
 *
 * Acknowledging records that somebody has seen the condition; the note is
 * optional. Dismissing says the alarm is wrong rather than the machine - a
 * limit set too tight, a channel known to misread - and the reason is
 * required, because the next reader of the history has to be able to tell a
 * wrong limit from a condition somebody chose to ignore. Neither clears the
 * condition: the reading coming back inside its limits does that.
 */
const ACTIONS = {
  acknowledge: {
    endpoint: ApiEndpoints.machine_health_anomaly_acknowledge,
    required: false
  },
  dismiss: {
    endpoint: ApiEndpoints.machine_health_anomaly_dismiss,
    required: true
  }
};

export type AlarmAction = keyof typeof ACTIONS;

export function AlarmNoteModal({
  action,
  target,
  onClose,
  onDone
}: Readonly<{
  action: AlarmAction;
  target: AlarmTarget | null;
  onClose: () => void;
  /** Called once the server has stored it. */
  onDone: () => void;
}>) {
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dismissing = action === 'dismiss';
  const missing = ACTIONS[action].required && !note.trim();

  const close = () => {
    setNote('');
    setError(null);
    onClose();
  };

  const submit = async () => {
    if (!target || missing) return;
    setBusy(true);
    setError(null);
    try {
      await api.post(
        apiUrl(ACTIONS[action].endpoint, target.machine, {
          anomalyId: target.anomaly
        }),
        { note }
      );
      setNote('');
      onDone();
    } catch (failure: any) {
      // The server's own words where it gave any: a permission refused, an
      // alarm that closed while the note was being written.
      setError(
        failure?.response?.data?.detail ??
          failure?.response?.data?.error ??
          t`The change was not stored.`
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      opened={target !== null}
      onClose={close}
      title={dismissing ? t`Dismiss alarm` : t`Acknowledge alarm`}
      centered
    >
      {target && (
        <Stack gap='sm'>
          <Text size='sm'>{target.summary}</Text>
          <Text size='xs' c='dimmed'>
            {dismissing
              ? t`Dismiss an alarm that is wrong rather than the machine: a limit set too tight, a channel known to misread. It leaves the active list and tells nobody while the reading goes on breaching, and closes when the reading returns inside its limits.`
              : t`Acknowledging records that you have seen this condition. It closes when the reading returns inside its limits, not before; acknowledging does not make a repair ready.`}
          </Text>
          <Textarea
            label={dismissing ? t`Reason` : t`Note`}
            placeholder={
              dismissing
                ? t`Why this alarm is not worth acting on`
                : t`What was seen, and what is being done`
            }
            value={note}
            onChange={(event) => setNote(event.currentTarget.value)}
            autosize
            minRows={3}
            maxLength={2000}
            required={ACTIONS[action].required}
            data-autofocus
          />
          {error && (
            <Alert color='red' variant='light'>
              {error}
            </Alert>
          )}
          <Group justify='flex-end'>
            <Button variant='subtle' onClick={close} disabled={busy}>
              {t`Cancel`}
            </Button>
            <Button
              onClick={submit}
              loading={busy}
              disabled={missing}
              color={dismissing ? 'gray' : undefined}
            >
              {dismissing ? t`Dismiss` : t`Acknowledge`}
            </Button>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
