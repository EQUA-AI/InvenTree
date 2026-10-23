import { t } from '@lingui/core/macro';
import { Alert, Button, Loader, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

export function HealthQuerySection({
  query,
  title,
  children
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    dataUpdatedAt: number;
    refetch: () => unknown;
  };
  title: string;
  children: ReactNode;
}) {
  if (query.isPending)
    return <Loader size='sm' aria-label={t`Loading health data`} />;
  if (query.isError) {
    return (
      <Alert color='red' title={title}>
        <Stack gap='xs'>
          <Text size='sm'>{t`This section could not be loaded. Its current state is unknown.`}</Text>
          {query.dataUpdatedAt > 0 && (
            <Text size='xs'>
              {t`Last successful load`}:{' '}
              {new Date(query.dataUpdatedAt).toLocaleString()}
            </Text>
          )}
          <Button
            variant='light'
            size='xs'
            onClick={() => query.refetch()}
            style={{ alignSelf: 'flex-start' }}
          >{t`Retry`}</Button>
        </Stack>
      </Alert>
    );
  }
  return children;
}
