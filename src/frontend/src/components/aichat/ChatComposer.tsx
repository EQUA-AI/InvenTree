import { t } from '@lingui/core/macro';
import { ActionIcon, Group, Paper, Textarea, Tooltip } from '@mantine/core';
import { IconPaperclip, IconPlayerStop, IconSend } from '@tabler/icons-react';
import type { KeyboardEvent, ReactNode, Ref } from 'react';
import workspaceStyles from './AssistantWorkspace.module.css';

export function ChatComposer({
  value,
  onChange,
  onKeyDown,
  inputRef,
  placeholder,
  disabled,
  attachDisabled,
  uploading,
  sendDisabled,
  loading,
  onAttach,
  onSend,
  onCancel,
  voice,
  maxRows = 6
}: {
  value: string;
  onChange: (value: string) => void;
  onKeyDown: (event: KeyboardEvent) => void;
  inputRef: Ref<HTMLTextAreaElement>;
  placeholder: string;
  disabled: boolean;
  attachDisabled: boolean;
  uploading: boolean;
  sendDisabled: boolean;
  loading: boolean;
  onAttach: () => void;
  onSend: () => void;
  onCancel: () => void;
  voice: ReactNode;
  maxRows?: number;
}) {
  return (
    <Paper
      className={workspaceStyles.composer}
      radius={16}
      withBorder
      p={6}
      data-testid='ai-chat-composer'
      data-voice-surface
      style={{
        background: 'var(--mantine-color-body)',
        boxShadow: 'var(--mantine-shadow-xs)'
      }}
    >
      <Textarea
        ref={inputRef}
        data-autofocus
        aria-label={t`Message`}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        onKeyDown={onKeyDown}
        autosize
        minRows={1}
        maxRows={maxRows}
        disabled={disabled}
        styles={{
          input: {
            border: 0,
            background: 'transparent',
            padding: '6px 8px',
            fontSize: 16,
            lineHeight: 1.5
          }
        }}
      />
      <Group justify='space-between' wrap='nowrap' gap={4}>
        <Group gap={4}>
          <Tooltip label={t`Attach file`}>
            <ActionIcon
              className={workspaceStyles.control}
              data-testid='attach-ai-chat-file'
              aria-label={t`Attach file`}
              size={36}
              variant='subtle'
              color='gray'
              onClick={onAttach}
              disabled={attachDisabled}
              loading={uploading}
            >
              <IconPaperclip size={18} />
            </ActionIcon>
          </Tooltip>
          {voice}
        </Group>
        {loading ? (
          <Tooltip label={t`Stop generating`}>
            <ActionIcon
              className={workspaceStyles.control}
              data-testid='cancel-ai-chat-turn'
              aria-label={t`Stop generating`}
              size={40}
              radius='md'
              color='red'
              variant='filled'
              onClick={onCancel}
            >
              <IconPlayerStop size={18} />
            </ActionIcon>
          </Tooltip>
        ) : (
          <Tooltip label={t`Send message`}>
            <ActionIcon
              className={workspaceStyles.control}
              data-testid='send-ai-chat-message'
              aria-label={t`Send message`}
              size={40}
              radius='md'
              variant='filled'
              onClick={onSend}
              disabled={sendDisabled}
            >
              <IconSend size={18} />
            </ActionIcon>
          </Tooltip>
        )}
      </Group>
    </Paper>
  );
}
