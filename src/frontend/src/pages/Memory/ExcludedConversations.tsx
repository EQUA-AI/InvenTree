import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Card,
  Group,
  NativeSelect,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useApi } from '../../contexts/ApiContext';

type Conversation = {
  thread_id: string;
  created_at: string;
  memory_mode: string;
  extraction_status: string;
};
type Page = { results: Conversation[]; next_cursor: string | null };
const ROOT = '/api/aichat/memory/';

export function ExcludedConversations() {
  const api = useApi();
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const ticket = useRef(0);
  const changing = useRef(false);

  const load = useCallback(
    async (cursor = '') => {
      const current = ++ticket.current;
      const signal = controller.current?.signal;
      setPage(null);
      setError(false);
      setBusy(true);
      try {
        const response = await api.get<Page>(`${ROOT}excluded-conversations/`, {
          params: { cursor },
          signal
        });
        if (!signal?.aborted && ticket.current === current)
          setPage(response.data);
      } catch {
        if (!signal?.aborted && ticket.current === current) setError(true);
      } finally {
        if (!signal?.aborted && ticket.current === current) setBusy(false);
      }
    },
    [api]
  );

  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    void load();
    return () => {
      abort.abort();
      ticket.current += 1;
    };
  }, [load]);

  const change = async (id: string, mode: string) => {
    if (changing.current) return;
    changing.current = true;
    ticket.current += 1;
    setBusy(true);
    setError(false);
    const signal = controller.current?.signal;
    try {
      await api.put(
        `${ROOT}conversations/${encodeURIComponent(id)}/mode/`,
        { memory_mode: mode },
        { signal }
      );
      if (!signal?.aborted) await load();
    } catch {
      if (!signal?.aborted) {
        setPage(null);
        setError(true);
      }
    } finally {
      changing.current = false;
      if (!signal?.aborted) setBusy(false);
    }
  };

  return (
    <Card withBorder>
      <Stack>
        <Title order={3}>{t`Excluded conversations`}</Title>
        <Text size='sm'>{t`These owned conversations are currently excluded from learning. Changing a setting does not acknowledge the notice or enroll a client. Confirmed memories and recall are controlled separately.`}</Text>
        {error && (
          <Alert color='orange'>{t`Conversation settings are unavailable. Refresh to retry.`}</Alert>
        )}
        {page?.results.map((row) => (
          <Card withBorder key={row.thread_id}>
            <Stack gap='xs'>
              <Text size='sm'>
                {t`Conversation created`}{' '}
                {new Date(row.created_at).toLocaleString()}
              </Text>
              <Text size='xs'>{row.thread_id}</Text>
              <Text size='sm'>
                {row.extraction_status === 'shared_thread'
                  ? t`Shared conversations cannot learn memories.`
                  : row.extraction_status === 'memory_off'
                    ? t`Learning is off for this conversation.`
                    : row.extraction_status === 'feature_disabled'
                      ? t`Memory learning is not enabled on this server.`
                      : t`Your notice, account settings, client enrollment or access currently excludes learning.`}
              </Text>
              <NativeSelect
                label={t`Learn memories from this conversation`}
                value={row.memory_mode}
                disabled={busy}
                onChange={(event) =>
                  void change(row.thread_id, event.currentTarget.value)
                }
                data={[
                  { value: 'inherit', label: t`Use my default` },
                  { value: 'extract', label: t`Learn from this conversation` },
                  {
                    value: 'off',
                    label: t`Do not learn from this conversation`
                  }
                ]}
              />
            </Stack>
          </Card>
        ))}
        {page && page.results.length === 0 && (
          <Text size='sm'>{t`No excluded conversations on this page.`}</Text>
        )}
        <Group>
          <Button
            variant='light'
            disabled={busy}
            onClick={() => void load()}
          >{t`Refresh conversations`}</Button>
          {page?.next_cursor && (
            <Button
              variant='light'
              disabled={busy}
              onClick={() => void load(page.next_cursor ?? '')}
            >{t`Next conversations`}</Button>
          )}
        </Group>
      </Stack>
    </Card>
  );
}
