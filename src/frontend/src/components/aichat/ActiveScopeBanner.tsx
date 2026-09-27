/**
 * The read-only analysis-context line near the composer (plan C2).
 *
 * The scope dropdown is gone: this line only REPORTS what the server
 * confirmed ("Analyzing: …" / "Analysis context not confirmed"). It never
 * offers selection, never derives scope from a page path or routing hint,
 * and never claims a machine restriction that was not applied. Scope
 * seeding, version checks and conflict retry stay in the send pipeline.
 */

import { t } from '@lingui/core/macro';
import { Group, Text } from '@mantine/core';
import { IconFocus2, IconWorld } from '@tabler/icons-react';

import type { ActiveThreadScope } from '../../hooks/UseAIChat';
import { analysisContext } from './scopeContext';

export function AnalysisContextLine({
  scope
}: Readonly<{ scope: ActiveThreadScope | null }>) {
  const context = analysisContext(scope);
  const unconfirmed = context.kind === 'unconfirmed';
  const label = unconfirmed
    ? t`Analysis context not confirmed`
    : context.kind === 'fleet'
      ? t`Analyzing: authorized machines`
      : context.label
        ? t`Analyzing: ${context.label}`
        : context.machineCount === 1
          ? t`Analyzing: 1 selected asset`
          : t`Analyzing: ${context.machineCount ?? 0} selected assets`;
  return (
    <Group gap={6} data-testid='ai-chat-scope-banner'>
      <Group gap={4} wrap='nowrap'>
        {unconfirmed ? <IconFocus2 size={12} /> : <IconWorld size={12} />}
        <Text size='xs' c='dimmed' data-testid='ai-chat-analysis-context'>
          <Text span inherit data-testid='ai-chat-scope-label'>
            {label}
          </Text>
        </Text>
      </Group>
    </Group>
  );
}
