import { t } from '@lingui/core/macro';

export type ThreadDeleteResult = 'deleted' | 'purge_incomplete' | 'error';

/** Shared copy for conversation deletion and its retry state. */
export function threadDeletionCopy(incomplete: boolean, persisted: boolean) {
  return {
    title: incomplete
      ? t`Finish deleting conversation`
      : t`Delete conversation?`,
    description: incomplete
      ? t`Deletion is not yet complete. Retry to finish removing the conversation and its associated data. The original choice about forgetting saved memories will be honored.`
      : persisted
        ? t`Delete this conversation, its messages and associated uploads? Shared access will also be removed. Confirmed memories remain unless you choose to forget them below. This cannot be undone.`
        : t`Delete this conversation from this browser? This cannot be undone.`,
    action: incomplete ? t`Retry cleanup` : t`Delete conversation`
  };
}
