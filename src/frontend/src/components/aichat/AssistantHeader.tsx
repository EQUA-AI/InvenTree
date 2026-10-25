import { t } from '@lingui/core/macro';
import { ActionIcon, Box, Group, Text, Tooltip } from '@mantine/core';
import {
  IconArrowsMaximize,
  IconArrowsMinimize,
  IconMessagePlus,
  IconRefresh,
  IconX
} from '@tabler/icons-react';
import type { ReactNode } from 'react';
import workspaceStyles from './AssistantWorkspace.module.css';

export function AssistantHeader({
  conversation,
  navigation,
  syncing,
  busy,
  expanded,
  canExpand,
  onSync,
  onNew,
  onExpand,
  onClose
}: {
  conversation: ReactNode;
  navigation: ReactNode;
  syncing: boolean;
  busy: boolean;
  expanded: boolean;
  canExpand: boolean;
  onSync: () => void;
  onNew: () => void;
  onExpand: () => void;
  onClose: () => void;
}) {
  return (
    <Box
      px='sm'
      pt='xs'
      pb='xs'
      style={{
        borderBottom: '1px solid var(--mantine-color-default-border)',
        flexShrink: 0
      }}
      data-testid='assistant-header'
    >
      <Group gap={4} justify='space-between' wrap='nowrap'>
        <Text
          size='sm'
          fw={650}
          style={{ flex: 1, minWidth: 0 }}
        >{t`AI Assistant`}</Text>
        <Group gap={2} wrap='nowrap'>
          <Tooltip label={t`Sync conversations`}>
            <ActionIcon
              className={workspaceStyles.control}
              data-testid='sync-ai-chat-threads'
              aria-label={t`Sync conversations`}
              variant='subtle'
              color='gray'
              size={36}
              loading={syncing}
              onClick={onSync}
            >
              <IconRefresh size={17} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label={t`New conversation`}>
            <ActionIcon
              className={workspaceStyles.control}
              data-testid='new-ai-chat-thread'
              aria-label={t`New conversation`}
              variant='subtle'
              color='gray'
              size={36}
              disabled={busy}
              onClick={onNew}
            >
              <IconMessagePlus size={18} />
            </ActionIcon>
          </Tooltip>
          {canExpand && (
            <Tooltip label={expanded ? t`Restore chat width` : t`Expand chat`}>
              <ActionIcon
                className={workspaceStyles.control}
                aria-label={expanded ? t`Restore chat width` : t`Expand chat`}
                variant='subtle'
                color='gray'
                size={36}
                onClick={onExpand}
              >
                {expanded ? (
                  <IconArrowsMinimize size={18} />
                ) : (
                  <IconArrowsMaximize size={18} />
                )}
              </ActionIcon>
            </Tooltip>
          )}
          <Tooltip label={t`Close`}>
            <ActionIcon
              className={workspaceStyles.control}
              data-testid='close-ai-chat'
              aria-label={t`Close chat`}
              variant='subtle'
              color='gray'
              size={36}
              onClick={onClose}
            >
              <IconX size={18} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
      {conversation && <Box mt={2}>{conversation}</Box>}
      <Box mt='xs'>{navigation}</Box>
    </Box>
  );
}
