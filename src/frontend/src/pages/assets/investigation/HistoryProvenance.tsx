import type { SeriesResponse } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { Alert, Button, Group, Paper, Stack, Text } from '@mantine/core';
import { formatDate } from '../../../defaults/formatters';
import { clockLabel } from '../performance/WindowStatus';
import type { DataRange } from '../performance/usePerformanceData';

export function HistoryProvenance({
  response,
  loading,
  updating,
  failed,
  extent,
  onRetry,
  currentDescription
}: Readonly<{
  response: SeriesResponse | undefined;
  loading: boolean;
  updating: boolean;
  failed: boolean;
  extent: DataRange | undefined;
  onRetry: () => void;
  currentDescription?: string;
}>) {
  const entries = response?.series ?? [];
  const unavailable = entries.filter((entry) => !entry.available);
  const empty = entries.filter(
    (entry) => entry.available && entry.samples.length === 0
  );
  return (
    <Paper withBorder radius='md' p='sm' data-testid='history-provenance'>
      <Stack gap={4}>
        <Text fw={600} size='sm'>{t`History origin and limits`}</Text>
        <Text size='xs' c='dimmed'>
          {currentDescription ??
            t`KPIs, current bars and the current-limit evaluation use each signal's latest observation, even for a fixed historical window. They are not the selected range. Window trends read source history; retained snapshots are separate evidence.`}
        </Text>
        {loading && <Text size='xs'>{t`Loading selected source window…`}</Text>}
        {failed && (
          <Alert color='red'>{t`History read failed. Current readings do not replace missing history.`}</Alert>
        )}
        {response && !failed && (
          <>
            <Text size='xs'>
              {t`Source`}:{' '}
              {[...new Set(entries.map((entry) => entry.source_name))].join(
                ', '
              ) || t`Not supplied`}
              . {t`Window`}: {new Date(response.window_start).toLocaleString()}{' '}
              — {new Date(response.window_end).toLocaleString()} (
              {clockLabel(Date.parse(response.window_end))}).{' '}
              {updating ? t`Previous window while updating.` : ''}
            </Text>
            <Text size='xs'>
              {response.read_batches && response.read_batches.length > 1 ? (
                t`Resolution is reported per bounded read below.`
              ) : (
                <>
                  {t`Resolution`}: {response.resolution_seconds} s
                </>
              )}{' '}
              ·{' '}
              {response.mode === 'sampled'
                ? t`Sampled real readings; fast transients may be absent.`
                : t`Complete source read for this bounded window; source gaps remain gaps.`}
            </Text>
            <Text size='xs'>
              {t`Single-window limit`}:{' '}
              {response.limits.max_window_seconds / 3600} h · {t`Point budget`}:{' '}
              {response.slots ?? t`Complete mode`} · {t`Returned series`}:{' '}
              {entries.length}
            </Text>
            {response.display_shifted && (
              <Alert color='yellow'>{t`Source timestamps are moved by ${response.display_shift_seconds} seconds onto the display clock. This is not the plant's original clock.`}</Alert>
            )}
            {(unavailable.length > 0 || empty.length > 0) && (
              <Alert color='gray' title={t`History coverage gaps`}>
                {unavailable.length > 0 && (
                  <details>
                    <summary>{t`${unavailable.length} unavailable series — show source reasons`}</summary>
                    {unavailable.map((entry) => (
                      <Text key={entry.binding_id} size='xs'>
                        {entry.display_name || entry.external_key}:{' '}
                        {entry.detail || entry.reason || t`History unavailable`}
                      </Text>
                    ))}
                  </details>
                )}
                {empty.length > 0 && (
                  <Text size='xs'>{t`${empty.length} series returned no samples. No samples is different from a recorded zero.`}</Text>
                )}
              </Alert>
            )}
            {!!response.read_batches?.some((batch) => batch.failed) && (
              <Alert color='yellow' title={t`Partial history read`}>
                {t`${response.read_batches.filter((batch) => batch.failed).length} of ${response.read_batches.length} bounded batches failed. Available charts remain visible; missing batches are not complete evidence.`}
                <Button
                  size='compact-xs'
                  variant='subtle'
                  onClick={onRetry}
                >{t`Retry history`}</Button>
              </Alert>
            )}
            {response.read_batches && response.read_batches.length > 1 && (
              <details>
                <summary>{t`Per-batch resolution and clock`}</summary>
                {response.read_batches.map((batch, i) => (
                  <Text size='xs' key={i}>
                    {t`Batch ${i + 1}`}:{' '}
                    {batch.failed
                      ? t`History read failed; coverage unknown.`
                      : t`Resolution ${batch.resolution_seconds ?? t`not supplied`} s; measured cadence ${batch.cadence_seconds ?? t`not supplied`} s; display shift ${batch.display_shift_seconds ?? t`not supplied`} s.`}
                  </Text>
                ))}
              </details>
            )}
            {response.read_batches && response.read_batches.length > 1 && (
              <Text
                size='xs'
                c='dimmed'
              >{t`${response.read_batches.length} bounded batches share these time bounds. Each may read overlapping source documents; batching does not imply flat source work or payload.`}</Text>
            )}
          </>
        )}
        <Text size='xs' c='dimmed'>
          {extent?.available && extent.from && extent.to
            ? t`Reported source extent: ${formatDate(extent.from, { showTime: true, showSeconds: true })} — ${formatDate(extent.to, { showTime: true, showSeconds: true })}. Availability within it is not guaranteed.`
            : t`Source extent unavailable. This does not prove that the source has no history.`}
        </Text>
        {failed && (
          <Group>
            <Button
              size='xs'
              variant='light'
              onClick={onRetry}
            >{t`Retry history`}</Button>
          </Group>
        )}
      </Stack>
    </Paper>
  );
}
