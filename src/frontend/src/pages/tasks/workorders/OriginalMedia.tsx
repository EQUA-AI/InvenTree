import type { CaptureOriginal } from '@lib/types/Closeout';
import { t } from '@lingui/core/macro';
import { Alert, Button, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { MissingValue } from '../../../components/items/ValueTooltip';
import { useApi } from '../../../contexts/ApiContext';
import { recordedTime } from './execution/ExecutionFacts';

/** Protected native download; audio is not fetched until the user asks. */
export function OriginalMedia({
  originals
}: Readonly<{ originals?: CaptureOriginal[] }>) {
  const api = useApi();
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const download = async (row: CaptureOriginal) => {
    setBusy(true);
    setError(false);
    try {
      const response = await api.get(row.url, { responseType: 'blob' });
      const url = URL.createObjectURL(response.data);
      const link = document.createElement('a');
      link.href = url;
      link.download = row.filename;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack gap={4}>
      {error && (
        <Alert color='red'>{t`Original media could not be read. Retry the download; no replacement media is shown.`}</Alert>
      )}
      {!originals?.length ? (
        <MissingValue
          reason={t`Original media is not supplied by this readout. Audio playback and timing are unavailable without a linked source.`}
        />
      ) : (
        originals.map((row) => (
          <Stack gap={2} key={row.id}>
            <Button
              variant='subtle'
              size='compact-sm'
              loading={busy}
              onClick={() => void download(row)}
              styles={{
                label: { whiteSpace: 'normal', overflowWrap: 'anywhere' }
              }}
            >
              {t`Download original`}: {row.filename}
            </Button>
            <Text size='xs' style={{ overflowWrap: 'anywhere' }}>
              {t`SHA-256`}: {row.content_hash}
            </Text>
            <Text size='xs'>
              {t`Recording started`}: {recordedTime(row.started_at)} ·{' '}
              {t`Duration (ms)`}: {row.duration_ms ?? t`Not recorded`}
            </Text>
          </Stack>
        ))
      )}
    </Stack>
  );
}
