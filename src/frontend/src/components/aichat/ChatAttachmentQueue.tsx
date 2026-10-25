import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Button,
  Group,
  Loader,
  Stack,
  Text,
  Tooltip
} from '@mantine/core';
import { IconX } from '@tabler/icons-react';

import type { ChatDraftAttachment } from '../../states/ChatDraftState';

/** The same RAM draft owns queued, failed and ready attachments. */
export function ChatAttachmentQueue({
  attachments,
  disabled,
  onRetry,
  onRemove
}: {
  attachments: ChatDraftAttachment[];
  disabled: boolean;
  onRetry: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  if (attachments.length === 0) return null;
  return (
    <Stack gap={4} mb='xs' aria-label={t`Attachments`}>
      {attachments.map((item) => {
        const name =
          item.status === 'ready' ? item.uploaded.filename : item.file.name;
        const status =
          item.status === 'ready'
            ? t`Ready`
            : item.status === 'failed'
              ? t`Upload failed`
              : item.status === 'queued'
                ? t`Queued`
                : t`Uploading`;
        return (
          <Group
            key={item.id}
            gap='xs'
            wrap='nowrap'
            data-testid='ai-chat-attachment'
          >
            {item.status === 'uploading' && <Loader size='xs' aria-hidden />}
            <Text
              size='sm'
              truncate
              title={name}
              style={{ flex: 1, minWidth: 0 }}
            >
              {name}
            </Text>
            <Text
              size='xs'
              c={item.status === 'failed' ? 'red' : 'dimmed'}
              component='output'
              aria-live='polite'
            >
              {status}
            </Text>
            {item.status === 'failed' && (
              <Button
                size='compact-xs'
                variant='subtle'
                disabled={disabled}
                onClick={() => onRetry(item.id)}
              >
                {t`Retry`}
              </Button>
            )}
            <Tooltip label={t`Remove ${name}`}>
              <ActionIcon
                size={32}
                variant='subtle'
                disabled={disabled}
                data-testid={
                  item.status === 'ready'
                    ? `remove-ai-chat-attachment-${item.uploaded.file_id}`
                    : undefined
                }
                aria-label={t`Remove ${name}`}
                onClick={() => onRemove(item.id)}
              >
                <IconX size={14} />
              </ActionIcon>
            </Tooltip>
          </Group>
        );
      })}
    </Stack>
  );
}
