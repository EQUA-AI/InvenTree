/**
 * VoiceContextBadge (WS5-T4): show which conversation voice is bound to.
 *
 * Unscoped sessions are labelled explicitly; record-grounded labels arrive
 * only with the external Scoped Chat substrate (#14) and are never inferred
 * client-side.
 */

import { Badge, Tooltip } from '@mantine/core';

export interface VoiceContextBadgeProps {
  threadId: string | null;
  scoped?: boolean;
}

export function VoiceContextBadge({
  threadId,
  scoped = false
}: Readonly<VoiceContextBadgeProps>) {
  if (!threadId) {
    return null;
  }
  return (
    <Tooltip
      label={
        scoped
          ? 'Voice is grounded to the pinned record conversation'
          : 'Voice is attached to your general assistant conversation'
      }
    >
      <Badge
        size='xs'
        variant='dot'
        color={scoped ? 'teal' : 'gray'}
        data-testid='voice-context-badge'
      >
        {scoped ? 'record context' : 'general context'}
      </Badge>
    </Tooltip>
  );
}
