import { t } from '@lingui/core/macro';
import { Alert, Button, Stack, Text } from '@mantine/core';
export function RecordCommandState({
  controller
}: Readonly<{
  controller: {
    notice: string;
    retained: unknown;
    pending: boolean;
    retry: () => unknown;
  };
}>) {
  return (
    <>
      {controller.notice && (
        <Alert color={controller.retained ? 'orange' : 'blue'}>
          <Stack gap='xs'>
            <Text size='sm' component='output'>
              {controller.notice}
            </Text>
            {!!controller.retained && (
              <Button
                variant='default'
                loading={controller.pending}
                onClick={() => controller.retry()}
              >{t`Retry retained operation`}</Button>
            )}
          </Stack>
        </Alert>
      )}
    </>
  );
}
