import { t } from '@lingui/core/macro';
import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useEffect, useRef, useState } from 'react';

import {
  type TranscriptExportCounts,
  TranscriptExportError
} from '../../functions/transcriptExport';

export function ExportThreadsModal({
  onExport,
  onClose
}: Readonly<{
  onExport: (signal: AbortSignal) => Promise<TranscriptExportCounts>;
  onClose: () => void;
}>) {
  const pending = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TranscriptExportCounts | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      pending.current?.abort();
    };
  }, []);
  const close = () => {
    pending.current?.abort();
    onClose();
  };
  const submit = async () => {
    if (pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 60_000);
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const counts = await onExport(controller.signal);
      if (alive.current && !controller.signal.aborted) setResult(counts);
    } catch (err) {
      if (alive.current)
        setError(
          err instanceof TranscriptExportError && err.code === 'too_large'
            ? t`This history is too large for a browser download. Ask an administrator for a transcript export.`
            : t`The export could not finish. No download was started. Please try again.`
        );
    } finally {
      window.clearTimeout(timeout);
      pending.current = null;
      if (alive.current) setBusy(false);
    }
  };
  return (
    <Modal opened title={t`Export my saved conversations`} onClose={close}>
      <Stack>
        <Text size='sm'>{t`Download a JSON file containing the titles, stored summaries and messages of conversations you own in your current workspace, including those not loaded in this list.`}</Text>
        <Text size='sm'>{t`Shared-with-me conversations, unsaved drafts, attachments, voice recordings, evidence, operational records and account data are excluded.`}</Text>
        <Text size='sm'>{t`The export reads saved data as it runs. Conversations and messages created after it starts are excluded; concurrent changes may affect the result.`}</Text>
        {error && (
          <Alert color='red' role='alert'>
            {error}
          </Alert>
        )}
        {result && (
          <Text
            component='output'
            aria-live='polite'
            size='sm'
          >{t`Download started: ${result.threads} conversations and ${result.messages} messages. Check your browser downloads.`}</Text>
        )}
        <Group justify='flex-end'>
          <Button variant='default' onClick={close}>
            {busy ? t`Cancel export` : t`Close`}
          </Button>
          <Button
            loading={busy}
            onClick={() => void submit()}
          >{t`Download JSON`}</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
