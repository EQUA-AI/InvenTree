import { t } from '@lingui/core/macro';
import { ActionIcon, Badge, Box, Group, Text } from '@mantine/core';
import { IconX } from '@tabler/icons-react';
import type { ActiveThreadScope } from '../../hooks/UseAIChat';
import type {
  AIChatRoutingHint,
  AIChatTargetHint
} from '../../states/AIChatState';
import { AnalysisContextLine } from './ActiveScopeBanner';
import { ConversationFocusControl } from './ConversationFocusControl';

export function ConversationContextBar({
  scope,
  pending,
  routingHint,
  dismiss
}: {
  scope: ActiveThreadScope | null;
  pending?: AIChatTargetHint;
  routingHint?: AIChatRoutingHint;
  dismiss: () => void;
}) {
  return (
    <Box
      px='sm'
      py={6}
      data-testid='conversation-context-bar'
      style={{
        flexShrink: 1,
        minHeight: 0,
        borderBottom: '1px solid var(--mantine-color-default-border)',
        maxHeight: '25vh',
        overflowY: 'auto'
      }}
    >
      {pending && (
        <Group
          gap={4}
          mb={4}
          data-testid={
            pending.kind === 'machine'
              ? 'ai-chat-routing-hint'
              : 'ai-chat-pending-target'
          }
          wrap='nowrap'
        >
          <Text size='xs' style={{ flex: 1, overflowWrap: 'anywhere' }}>
            {pending.label} · {t`Confirming on send`}
          </Text>
          <ActionIcon
            variant='subtle'
            color='gray'
            size={28}
            data-testid='dismiss-routing-hint'
            aria-label={t`Dismiss pending target`}
            onClick={dismiss}
          >
            <IconX size={14} />
          </ActionIcon>
        </Group>
      )}
      {!pending && routingHint && (
        <Badge variant='outline'>{routingHint.machineName}</Badge>
      )}
      <ConversationFocusControl />
      <AnalysisContextLine scope={scope} />
    </Box>
  );
}
