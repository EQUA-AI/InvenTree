import { t } from '@lingui/core/macro';

export type ThreadDeleteResult = 'deleted' | 'purge_incomplete' | 'error';

/** Shared copy for conversation deletion and its retry state. */
export function threadDeletionCopy(incomplete: boolean, persisted: boolean) {
  return {
    title: incomplete
      ? t`Finish deleting conversation`
      : t`Delete conversation?`,
    description: incomplete
      ? t`The conversation has been removed, but some associated data still needs cleanup. Retry to finish deletion.`
      : persisted
        ? t`Delete this conversation, its messages and associated uploads? Shared access will also be removed. This cannot be undone.`
        : t`Delete this conversation from this browser? This cannot be undone.`,
    action: incomplete ? t`Retry cleanup` : t`Delete conversation`
  };
}
