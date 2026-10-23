import { t } from '@lingui/core/macro';
import { Alert, Button, Loader } from '@mantine/core';
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
        <Button
          variant='light'
          size='xs'
          onClick={() => query.refetch()}
          style={{ alignSelf: 'flex-start' }}
        >{t`Retry`}</Button>
      </Alert>
    );
  }
  return children;
}
