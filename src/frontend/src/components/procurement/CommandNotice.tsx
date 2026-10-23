import { t } from '@lingui/core/macro';
import { Alert, Button, Stack } from '@mantine/core';
import type { useProcurementCommand } from './useProcurementCommand';
export function CommandNotice({
  command
}: Readonly<{ command: ReturnType<typeof useProcurementCommand> }>) {
  return command.notice || command.retained ? (
    <Alert color={command.retained ? 'orange' : 'blue'}>
      <Stack gap='xs'>
        {command.notice}
        {command.retained && (
          <Button
            variant='light'
            size='xs'
            loading={command.pending}
            onClick={() => void command.retry()}
          >{t`Retry original operation`}</Button>
        )}
      </Stack>
    </Alert>
  ) : null;
}
