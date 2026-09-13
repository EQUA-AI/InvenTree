/**
 * VoiceContextBadge (WS5-T4): show which conversation voice is bound to.
 *
 * Unscoped sessions are labelled explicitly; record-grounded labels arrive
 * only with the external Scoped Chat substrate (#14) and are never inferred
 * client-side.
 */

import { t } from '@lingui/core/macro';
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
          ? t`Voice is grounded to the pinned record conversation`
          : t`Voice is attached to your general assistant conversation`
      }
    >
      <Badge
        size='xs'
        variant='dot'
        color={scoped ? 'teal' : 'gray'}
        data-testid='voice-context-badge'
      >
        {scoped ? t`Record context` : t`General context`}
      </Badge>
    </Tooltip>
  );
}
