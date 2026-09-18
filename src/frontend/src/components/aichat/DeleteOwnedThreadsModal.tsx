import { t } from '@lingui/core/macro';
import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useEffect, useRef, useState } from 'react';

import type {
  OwnedDeletionPage,
  OwnedDeletionPlan
} from '../../functions/ownedThreadDeletion';

/** Continuations stay in RAM for this session, never in shared browser storage. */
export function DeleteOwnedThreadsModal({
  onPrepare,
  onDeletePage,
  onClose
}: Readonly<{
  onPrepare: () => Promise<OwnedDeletionPlan>;
  onDeletePage: (
    plan: OwnedDeletionPlan,
    cursor: string | null
  ) => Promise<OwnedDeletionPage>;
  onClose: () => void;
}>) {
  const alive = useRef(true);
  const running = useRef(false);
  const plan = useRef<OwnedDeletionPlan | null>(null);
  const cursor = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<OwnedDeletionPage | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const submit = async () => {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError(false);
    try {
      if (!plan.current) plan.current = await onPrepare();
      // A bounded pass avoids an endless scan; further pages require Continue.
      // On transport failure, keep the last acknowledged cursor. On completed
      // but incomplete scans, restart the same token to re-probe earlier failures.
      for (let pageNumber = 0; pageNumber < 10 && alive.current; pageNumber++) {
        const next = await onDeletePage(plan.current, cursor.current);
        if (!alive.current) return;
        setReceipt(next);
        cursor.current = next.next_cursor;
        if (!next.next_cursor) break;
      }
    } catch {
      if (alive.current) setError(true);
    } finally {
      running.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const complete = receipt?.status === 'deleted';
  const started = Boolean(receipt || error);
  return (
    <Modal
      opened
      title={t`Delete my saved conversations?`}
      onClose={() => {
        if (!running.current) onClose();
      }}
      closeOnClickOutside={!busy}
      closeOnEscape={!busy}
      withCloseButton={!busy}
    >
      <Stack>
        <Text size='sm'>{t`Delete all saved conversations you own in your current workspace, including conversations not loaded in this list, their messages and associated uploads? Shared access to these conversations will be removed. This cannot be undone.`}</Text>
        <Text size='sm'>{t`Conversations shared with you, unsaved drafts and conversations created after deletion starts are kept. This does not erase your account or operational records.`}</Text>
        <Text size='sm'>{t`For a large history, use Continue deletion until completion is shown. Keep this dialog open to retain the same retry selection.`}</Text>
        {receipt && (
          <Text
            component='output'
            aria-live='polite'
            size='sm'
          >{t`${receipt.processed} conversations checked; ${receipt.incomplete} need cleanup.`}</Text>
        )}
        {complete && (
          <Alert
            color='green'
            aria-live='polite'
          >{t`Deletion completed for the selected conversations.`}</Alert>
        )}
        {!busy && receipt && !complete && !receipt.next_cursor && (
          <Alert
            color='yellow'
            aria-live='polite'
          >{t`Some data still needs cleanup. Retry to check the same conversations again.`}</Alert>
        )}
        {error && (
          <Alert
            color='red'
            role='alert'
          >{t`Deletion could not finish. Retry uses the same selection. If your workspace changed or the request expired, close this dialog and review a new deletion.`}</Alert>
        )}
        {!busy && started && !complete && (
          <Text
            size='xs'
            c='dimmed'
          >{t`Closing keeps deletions already made but discards this retry selection. Reopening starts a new selection.`}</Text>
        )}
        <Group justify='flex-end'>
          <Button variant='default' disabled={busy} onClick={onClose}>
            {complete || started ? t`Close` : t`Cancel`}
          </Button>
          {!complete && (
            <Button color='red' loading={busy} onClick={() => void submit()}>
              {error
                ? t`Retry deletion`
                : receipt?.next_cursor
                  ? t`Continue deletion`
                  : receipt
                    ? t`Retry cleanup`
                    : t`Delete my saved conversations`}
            </Button>
          )}
        </Group>
      </Stack>
    </Modal>
  );
}
