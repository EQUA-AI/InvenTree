import { t } from '@lingui/core/macro';
import {
  Alert,
  Box,
  Button,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { IconPaperclip } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../../contexts/ApiContext';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import { AttachmentTable } from '../../../tables/general/AttachmentTable';

export function WorkAttachmentResource({
  workOrderId
}: Readonly<{ workOrderId: number }>) {
  const api = useApi();
  const identity = useUserState((state) => state.authGeneration);
  const actor = useUserState((state) => state.user?.pk);
  const host = useLocalState((state) => state.hostKey);
  const [opened, setOpened] = useState(false);
  const [visited, setVisited] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const resourceId = useId();
  const count = useQuery<number>({
    queryKey: ['work-attachment-count', host, actor, identity, workOrderId],
    retry: false,
    queryFn: async ({ signal }) => {
      const response = await api.get(apiUrl(ApiEndpoints.attachment_list), {
        signal,
        params: {
          model_type: ModelType.workorder,
          model_id: workOrderId,
          limit: 1
        }
      });
      const total = response.data?.count;
      if (!Number.isSafeInteger(total) || total < 0)
        throw new Error('Attachment count unavailable');
      return total;
    }
  });
  useEffect(() => {
    if (opened && count.isSuccess && !count.isFetching) setVisited(true);
  }, [opened, count.isSuccess, count.isFetching]);
  const refreshCount = useCallback(() => {
    void count.refetch();
  }, [count.refetch]);
  const close = () => {
    setOpened(false);
    refreshCount();
    trigger.current?.focus({ preventScroll: true });
  };
  return (
    <Stack gap='xs'>
      <Group>
        <Button
          ref={trigger}
          variant='default'
          leftSection={
            count.isFetching ? (
              <Loader size={16} />
            ) : (
              <IconPaperclip size={16} />
            )
          }
          aria-expanded={opened}
          aria-controls={resourceId}
          onClick={() => {
            if (opened) close();
            else {
              setOpened(true);
              refreshCount();
            }
          }}
        >
          {count.isError
            ? t`Attachments (unavailable)`
            : count.isPending || count.isFetching
              ? t`Attachments`
              : t`Attachments (${count.data})`}
        </Button>
      </Group>
      <Box id={resourceId} hidden={!opened}>
        <Paper withBorder p='md'>
          <Stack gap='md'>
            <Group justify='space-between'>
              <Title order={4}>{t`Work attachments`}</Title>
              <Button
                variant='subtle'
                onClick={close}
              >{t`Close attachments`}</Button>
            </Group>
            {count.isFetching && <Loader size='sm' />}
            {count.isError && (
              <Alert color='red' title={t`Attachments unavailable`}>
                <Stack gap='xs'>
                  <Text>{t`The authorized attachment count could not be loaded. No cached files are shown.`}</Text>
                  <Button
                    variant='light'
                    w='fit-content'
                    onClick={refreshCount}
                  >{t`Retry attachments`}</Button>
                </Stack>
              </Alert>
            )}
            <Box hidden={count.isError || count.isFetching}>
              {visited && (
                <AttachmentTable
                  model_type={ModelType.workorder}
                  model_id={workOrderId}
                  onChange={refreshCount}
                />
              )}
            </Box>
          </Stack>
        </Paper>
      </Box>
    </Stack>
  );
}
