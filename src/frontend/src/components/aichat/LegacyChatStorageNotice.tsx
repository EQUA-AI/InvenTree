import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Checkbox,
  Group,
  Modal,
  Stack,
  Text
} from '@mantine/core';
import { useEffect, useState } from 'react';

import {
  LEGACY_CHAT_KEY,
  readLegacyChatArchive,
  removeLegacyChatArchive
} from '../../functions/chatThreadCache';

/** Preserve the only copy of old local-only history until the user resolves it. */
export function LegacyChatStorageNotice() {
  const [available, setAvailable] = useState(
    () => readLegacyChatArchive() !== null
  );
  const [opened, setOpened] = useState(false);
  const [keepCopy, setKeepCopy] = useState(false);
  const [downloaded, setDownloaded] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const refresh = (event: StorageEvent) => {
      if (event.key === LEGACY_CHAT_KEY || event.key === null) {
        setAvailable(readLegacyChatArchive() !== null);
      }
    };
    window.addEventListener('storage', refresh);
    return () => window.removeEventListener('storage', refresh);
  }, []);
  if (!available) return null;

  const download = () => {
    setFailed(false);
    try {
      const archive = readLegacyChatArchive();
      if (archive === null) {
        setAvailable(false);
        return;
      }
      const url = URL.createObjectURL(
        new Blob([archive], { type: 'application/json' })
      );
      const link = document.createElement('a');
      link.href = url;
      link.download = 'aimms-legacy-browser-conversations.json';
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setDownloaded(true);
    } catch {
      setFailed(true);
    }
  };

  return (
    <>
      <Alert color='blue' title={t`Older browser history`} mb='sm'>
        <Stack gap='xs'>
          <Text size='sm'>{t`Older conversations are saved in this browser. Review the backup options before removing them.`}</Text>
          <Button
            variant='light'
            size='xs'
            onClick={() => setOpened(true)}
          >{t`Review browser history`}</Button>
        </Stack>
      </Alert>
      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        title={t`Older browser history`}
      >
        <Stack>
          <Text size='sm'>{t`This older history is not linked to an account. It stays out of your current conversations. If it belongs to you, download a backup to keep any conversations that exist only in this browser.`}</Text>
          <Button
            variant='default'
            onClick={download}
          >{t`Download browser history`}</Button>
          {downloaded && (
            <Text size='sm'>{t`The download was started. Check that your backup file was saved before removing the browser copy.`}</Text>
          )}
          <Checkbox
            checked={keepCopy}
            onChange={(event) => setKeepCopy(event.currentTarget.checked)}
            label={t`I have saved anything I need, or I do not need this older history.`}
          />
          {failed && (
            <Alert
              color='red'
              role='alert'
            >{t`The browser operation failed. The older history has not been removed.`}</Alert>
          )}
          <Group justify='flex-end'>
            <Button
              variant='default'
              onClick={() => setOpened(false)}
            >{t`Keep for now`}</Button>
            <Button
              color='red'
              disabled={!keepCopy}
              onClick={() => {
                if (removeLegacyChatArchive()) {
                  setAvailable(false);
                  setOpened(false);
                } else setFailed(true);
              }}
            >{t`Remove older browser history`}</Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
