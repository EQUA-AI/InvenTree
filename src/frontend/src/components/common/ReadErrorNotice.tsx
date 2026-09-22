import { t } from '@lingui/core/macro';
import { Alert, Button } from '@mantine/core';
import { readFailure } from '../../functions/readQueryPolicy';

/** Never present an unavailable collection as an empty successful result. */
export function ReadErrorNotice({
  error,
  retry,
  stale = false
}: {
  error: unknown;
  retry: () => void;
  stale?: boolean;
}) {
  const kind = readFailure(error);
  const message =
    kind === 'authentication'
      ? t`Your session has expired. Sign in again, then retry.`
      : kind === 'forbidden'
        ? t`Unavailable for your current role or maintenance scope.`
        : kind === 'unsupported'
          ? t`This feature is unavailable on the selected server.`
          : kind === 'invalid'
            ? t`The server could not provide a valid result. Check your filters or contact your administrator.`
            : stale
              ? t`Refresh failed. Last successful values may be stale.`
              : t`Unable to load this information. Please retry.`;
  return (
    <Alert color='yellow' data-testid='read-error-notice'>
      {message}
      <Button
        size='compact-xs'
        variant='subtle'
        onClick={retry}
      >{t`Retry`}</Button>
    </Alert>
  );
}
