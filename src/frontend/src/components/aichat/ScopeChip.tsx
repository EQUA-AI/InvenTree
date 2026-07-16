/**
 * Persistent, non-dismissable scope chip for a pinned conversation.
 *
 * The chip is truthful by construction: its label comes from the
 * server-resolved context descriptor, never from browser state.
 */

import { t } from '@lingui/core/macro';
import { Badge, Group, Text, Tooltip } from '@mantine/core';
import { IconPin } from '@tabler/icons-react';

export function ScopeChip({
  label,
  asOf,
  revoked = false
}: Readonly<{
  label: string;
  asOf?: string;
  revoked?: boolean;
}>) {
  const badge = (
    <Badge
      color={revoked ? 'red' : 'blue'}
      variant='light'
      size='lg'
      radius='sm'
      leftSection={<IconPin size={12} aria-hidden />}
      data-testid='scoped-chat-chip'
      aria-label={t`Conversation pinned to ${label}`}
    >
      <Group gap={4} wrap='nowrap'>
        <Text size='xs' fw={600} truncate style={{ maxWidth: 320 }}>
          {label}
        </Text>
      </Group>
    </Badge>
  );

  if (!asOf) {
    return badge;
  }
  const stamp = new Date(asOf).toLocaleString();
  return (
    <Tooltip
      label={
        revoked
          ? t`Access to this record was revoked`
          : t`Context resolved ${stamp}`
      }
    >
      {badge}
    </Tooltip>
  );
}
