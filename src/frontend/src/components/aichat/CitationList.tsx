/**
 * Numbered citations with source labels and as-of times.
 *
 * Citations are re-filtered server-side against the viewer's current
 * authorization: a revoked source renders as unavailable, never as content.
 */

import { t } from '@lingui/core/macro';
import { Group, Stack, Text } from '@mantine/core';
import { IconLink, IconLock } from '@tabler/icons-react';

// Owned here since S14 removed the scoped-chat rail; the drawer maps its
// evidence payloads into this shape.
export interface ScopedCitation {
  id: number;
  turn_key: string;
  source_type: string;
  available: boolean;
  as_of: string;
  source_id?: string;
  source_revision?: string;
  locator?: Record<string, any>;
  excerpt_hash?: string;
}

export function CitationList({
  citations
}: Readonly<{ citations: ScopedCitation[] }>) {
  if (citations.length === 0) {
    return null;
  }
  return (
    <Stack gap={2} data-testid='scoped-chat-citations'>
      <Text size='xs' fw={600} c='dimmed'>
        {t`Sources`}
      </Text>
      {citations.map((citation, index) => {
        const stamp = new Date(citation.as_of).toLocaleString();
        if (!citation.available) {
          return (
            <Group key={citation.id} gap={4} wrap='nowrap'>
              <IconLock size={12} aria-hidden />
              <Text size='xs' c='dimmed'>
                [{index + 1}] {t`Source unavailable`} — {stamp}
              </Text>
            </Group>
          );
        }
        const toolName = citation.locator?.tool ?? citation.source_type;
        return (
          <Group key={citation.id} gap={4} wrap='nowrap'>
            <IconLink size={12} aria-hidden />
            <Text size='xs' c='dimmed'>
              [{index + 1}] {toolName} · {citation.source_id} ·{' '}
              {citation.source_revision} — {t`as of`} {stamp}
            </Text>
          </Group>
        );
      })}
    </Stack>
  );
}
