import { t } from '@lingui/core/macro';
import { Alert, Button, Stack, Text } from '@mantine/core';
import { ErrorBoundary, type FallbackRender } from '@sentry/react';
import { IconExclamationCircle, IconInfoCircle } from '@tabler/icons-react';
import { type ReactNode, useCallback, useState } from 'react';
import { isChunkLoadError } from '../functions/ChunkError';

export function DefaultFallback({
  title,
  error
}: Readonly<{ title: string; error: string | null }>): ReactNode {
  if (isChunkLoadError(error)) {
    return (
      <Alert
        color='yellow'
        icon={<IconExclamationCircle />}
        title={t`Page resources could not be loaded`}
      >
        <Stack gap='xs'>
          <Text size='sm'>
            {t`This can happen after an update or a connection problem. Reload this page to try again. Unsaved changes may be lost.`}
          </Text>
          <Button onClick={() => window.location.reload()}>
            {t`Reload page`}
          </Button>
        </Stack>
      </Alert>
    );
  }
  return (
    <>
      <Alert
        color='red'
        icon={<IconExclamationCircle />}
        title={`INVE-E17: ${t`Error rendering component`}: ${title}`}
      >
        <Stack gap='xs'>
          <Text size='sm'>
            {t`An error occurred while rendering this component.`}
          </Text>
          <Text size='sm'>
            {t`Try reloading the page, or contact your administrator if the problem persists.`}
          </Text>
        </Stack>
      </Alert>
      {error && (
        <Alert color='red' icon={<IconInfoCircle />} title={t`Error Details`}>
          <Text size='sm'>{error}</Text>
        </Alert>
      )}
    </>
  );
}

export function Boundary({
  children,
  label,
  fallback
}: Readonly<{
  children: ReactNode;
  label: string;
  fallback?: React.ReactElement<any> | FallbackRender;
}>): ReactNode {
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const onError = useCallback(
    (error: unknown, componentStack: string | undefined, eventId: string) => {
      console.error(`ERR: Error rendering component: ${label}`);
      console.error(error);
      setErrorMessage(error instanceof Error ? error.message : String(error));
    },
    []
  );

  return (
    <ErrorBoundary
      fallback={
        fallback ?? <DefaultFallback title={label} error={errorMessage} />
      }
      onError={onError}
    >
      {children}
    </ErrorBoundary>
  );
}
