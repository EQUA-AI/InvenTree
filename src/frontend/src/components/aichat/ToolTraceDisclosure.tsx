/**
 * Collapsible, redacted tool-invocation trace for one scoped conversation.
 */

import { t } from '@lingui/core/macro';
import {
  Badge,
  Collapse,
  Group,
  Stack,
  Text,
  UnstyledButton
} from '@mantine/core';
import { IconChevronDown, IconChevronRight } from '@tabler/icons-react';
import { useState } from 'react';

import type { ScopedToolTraceRow } from '../../hooks/UseScopedChat';

export function ToolTraceDisclosure({
  rows
}: Readonly<{ rows: ScopedToolTraceRow[] }>) {
  const [open, setOpen] = useState(false);

  if (rows.length === 0) {
    return null;
  }
  return (
    <Stack gap={2} data-testid='scoped-chat-tool-trace'>
      <UnstyledButton
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-label={t`Toggle tool trace`}
      >
        <Group gap={4} wrap='nowrap'>
          {open ? (
            <IconChevronDown size={12} aria-hidden />
          ) : (
            <IconChevronRight size={12} aria-hidden />
          )}
          <Text size='xs' fw={600} c='dimmed'>
            {t`Tool trace`} ({rows.length})
          </Text>
        </Group>
      </UnstyledButton>
      <Collapse expanded={open}>
        <Stack gap={2}>
          {rows.map((row) => (
            <Group key={row.id} gap={6} wrap='nowrap'>
              <Badge
                size='xs'
                color={row.authorization_result === 'allowed' ? 'teal' : 'red'}
              >
                {row.authorization_result}
              </Badge>
              <Text size='xs' c='dimmed'>
                {row.tool} v{row.tool_version} · {JSON.stringify(row.arguments)}{' '}
                · {row.duration_ms != null ? `${row.duration_ms}ms · ` : ''}
                {new Date(row.created_at).toLocaleTimeString()}
              </Text>
            </Group>
          ))}
        </Stack>
      </Collapse>
    </Stack>
  );
}
