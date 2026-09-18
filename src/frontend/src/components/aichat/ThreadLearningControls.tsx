import { t } from '@lingui/core/macro';
import { Alert, Anchor, NativeSelect, Stack, Text } from '@mantine/core';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { csrfHeaders } from '../../hooks/UseAIChat';

type Learning = {
  memory_mode: 'inherit' | 'extract' | 'off';
  effective_mode: string;
  extraction_status: string;
  recall_status: string;
};

export function ThreadLearningControls({
  threadId,
  host
}: Readonly<{ threadId: string; host: string }>) {
  const [value, setValue] = useState<Learning | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const pending = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    setValue(null);
    setError(false);
    void fetch(`${host}/threads/${encodeURIComponent(threadId)}/learning`, {
      credentials: 'include',
      cache: 'no-store',
      signal: abort.signal
    })
      .then(async (response) => {
        if (!response.ok) throw new Error('learning_unavailable');
        const result: Learning = await response.json();
        if (!abort.signal.aborted) setValue(result);
      })
      .catch(() => {
        if (!abort.signal.aborted) setError(true);
      });
    return () => abort.abort();
  }, [threadId, host]);

  const change = async (mode: string) => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(false);
    const signal = controller.current?.signal;
    try {
      const response = await fetch(
        `${host}/threads/${encodeURIComponent(threadId)}/learning`,
        {
          method: 'PUT',
          credentials: 'include',
          signal,
          headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
          body: JSON.stringify({ memory_mode: mode })
        }
      );
      if (!response.ok) throw new Error('learning_unavailable');
      const result: Learning = await response.json();
      if (!signal?.aborted) setValue(result);
    } catch {
      if (!signal?.aborted) {
        setValue(null);
        setError(true);
      }
    } finally {
      pending.current = false;
      if (!signal?.aborted) setBusy(false);
    }
  };

  return (
    <Stack gap='xs'>
      <Anchor
        component={Link}
        to='/memory/'
      >{t`What AIMMS remembers across conversations`}</Anchor>
      {error && (
        <Alert color='orange'>{t`Learning settings are unavailable. Close and reopen this conversation's memory panel to reload.`}</Alert>
      )}
      {value && (
        <>
          <NativeSelect
            label={t`Learn memories from this conversation`}
            value={value.memory_mode}
            disabled={busy || value.extraction_status === 'shared_thread'}
            onChange={(event) => void change(event.currentTarget.value)}
            data={[
              { value: 'inherit', label: t`Use my default` },
              { value: 'extract', label: t`Learn from this conversation` },
              { value: 'off', label: t`Do not learn from this conversation` }
            ]}
          />
          <Text size='xs'>
            {value.extraction_status === 'eligible'
              ? t`This conversation is eligible for learning.`
              : value.extraction_status === 'shared_thread'
                ? t`Shared conversations cannot learn or recall personal memories.`
                : t`Learning is currently excluded by your settings, notice, client enrollment or access.`}
          </Text>
          <Text size='xs'>{t`Turning learning off withdraws pending suggestions. Confirmed memories and conversation summaries remain. It does not turn off recall of existing memories.`}</Text>
        </>
      )}
    </Stack>
  );
}
