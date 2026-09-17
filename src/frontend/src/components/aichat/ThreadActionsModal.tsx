import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Modal,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useState } from 'react';

import type { ChatThread } from '../../hooks/UseAIChat';
import { type ThreadDeleteResult, threadDeletionCopy } from './threadDeletion';

export type ThreadAction = {
  kind: 'rename' | 'share' | 'delete';
  thread: ChatThread;
};

/** Mount with a key per action so drafts/errors never transfer to another row. */
export function ThreadActionsModal({
  action,
  onClose,
  onRename,
  onDelete,
  onShare
}: Readonly<{
  action: ThreadAction;
  onClose: () => void;
  onRename: (id: string, title: string) => Promise<boolean>;
  onDelete: (id: string) => Promise<ThreadDeleteResult>;
  onShare?: (
    id: string,
    username: string,
    revoke: boolean
  ) => Promise<{ ok: boolean }>;
}>) {
  const [value, setValue] = useState(
    action.kind === 'rename' ? action.thread.title : ''
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [incomplete, setIncomplete] = useState(
    Boolean(action.thread.deletionPending)
  );
  const deletion = threadDeletionCopy(
    incomplete,
    Boolean(action.thread.isPersisted)
  );

  const submit = async (revoke = false) => {
    if (busy || (action.kind !== 'delete' && !value.trim())) return;
    setBusy(true);
    setError(null);
    try {
      if (action.kind === 'delete') {
        const result = await onDelete(action.thread.id);
        if (result === 'purge_incomplete') {
          setIncomplete(true);
          return;
        }
        if (result !== 'deleted') throw new Error('delete');
      } else if (action.kind === 'rename') {
        if (!(await onRename(action.thread.id, value.trim())))
          throw new Error('rename');
      } else if (
        !(await onShare?.(action.thread.id, value.trim(), revoke))?.ok
      ) {
        throw new Error('share');
      }
      onClose();
    } catch {
      setError(t`The change could not be completed. Please try again.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      opened
      onClose={() => {
        if (!busy) onClose();
      }}
      title={
        action.kind === 'delete'
          ? deletion.title
          : action.kind === 'rename'
            ? t`Rename conversation`
            : t`Share conversation`
      }
      closeOnClickOutside={!busy}
      closeOnEscape={!busy}
      withCloseButton={!busy}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Stack>
          <Text size='sm' lineClamp={2}>
            {action.thread.title}
          </Text>
          {action.kind === 'delete' ? (
            <Text size='sm'>{deletion.description}</Text>
          ) : (
            <TextInput
              data-autofocus
              label={
                action.kind === 'rename' ? t`Conversation title` : t`Username`
              }
              description={
                action.kind === 'share'
                  ? t`Grant read-only access or revoke existing access.`
                  : undefined
              }
              value={value}
              maxLength={action.kind === 'rename' ? 255 : 150}
              required
              disabled={busy}
              onChange={(event) => setValue(event.currentTarget.value)}
            />
          )}
          {error && (
            <Alert color='red' role='alert'>
              {error}
            </Alert>
          )}
          <Group justify='flex-end'>
            <Button
              variant='default'
              disabled={busy}
              onClick={onClose}
            >{t`Cancel`}</Button>
            {action.kind === 'share' && (
              <Button
                variant='light'
                color='red'
                disabled={busy || !value.trim()}
                onClick={() => void submit(true)}
              >{t`Revoke access`}</Button>
            )}
            <Button
              type='submit'
              color={action.kind === 'delete' ? 'red' : undefined}
              loading={busy}
              disabled={action.kind !== 'delete' && !value.trim()}
            >
              {action.kind === 'delete'
                ? deletion.action
                : action.kind === 'rename'
                  ? t`Save`
                  : t`Share read-only`}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
