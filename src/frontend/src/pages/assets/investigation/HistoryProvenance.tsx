import type { SeriesResponse } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Paper,
  Spoiler,
  Stack,
  Text
} from '@mantine/core';
import { displayInstant } from '../displayClock';
import { AttentionAlert, MetaText } from '../health/common';
import monitoring from '../monitoring.module.css';
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
    <Paper withBorder p='sm' data-testid='history-provenance'>
      <Stack gap={4}>
        <Text fw={600} size='sm'>{t`History origin and limits`}</Text>
        <MetaText maw='70ch' size='sm'>
          {currentDescription ??
            t`KPIs, current dots and limit assessments use latest observations, not the selected range. Window trends read source history; retained snapshots are separate evidence.`}
        </MetaText>
        {loading && (
          <Text maw='70ch' size='sm'>{t`Loading selected source window…`}</Text>
        )}
        {failed && (
          <Alert color='red'>{t`History read failed. Current readings do not replace missing history.`}</Alert>
        )}
        {response && !failed && (
          <>
            <MetaText maw='70ch'>
              {t`Source`}:{' '}
              {[...new Set(entries.map((entry) => entry.source_name))].join(
                ', '
              ) || t`Not supplied`}{' '}
              ·{' '}
              {Math.round(
                (Date.parse(response.window_end) -
                  Date.parse(response.window_start)) /
                  60000
              )}{' '}
              {t`min window`} · {t`measured interval`}{' '}
              {response.read_batches && response.read_batches.length > 1
                ? t`per read`
                : `${response.resolution_seconds} s`}{' '}
              · {t`gaps not filled`}
            </MetaText>
            <MetaText maw='70ch' size='xs'>
              {response.mode === 'sampled'
                ? t`Sampled real readings; fast transients may be absent.`
                : t`Complete source read for this bounded window; source gaps remain gaps.`}
            </MetaText>
            <Spoiler
              maxHeight={0}
              classNames={{ control: monitoring.link }}
              showLabel={t`Show read details`}
              hideLabel={t`Hide read details`}
            >
              <Text maw='70ch' size='xs'>
                {t`Window`}: {displayInstant(response.window_start)} —{' '}
                {displayInstant(response.window_end)}.{' '}
                {updating ? t`Previous window while updating.` : ''}
              </Text>
              <Text maw='70ch' size='xs'>
                {t`Single-window limit`}:{' '}
                {response.limits.max_window_seconds / 3600} h ·{' '}
                {t`Samples requested`}: {response.slots ?? t`Complete mode`} ·{' '}
                {t`Returned series`}: {entries.length}
              </Text>
            </Spoiler>
            {response.display_shifted && (
              <AttentionAlert>{t`Source timestamps are moved by ${response.display_shift_seconds} seconds onto the display clock. This is not the plant's original clock.`}</AttentionAlert>
            )}
            {(unavailable.length > 0 || empty.length > 0) && (
              <Alert color='gray' title={t`History coverage gaps`}>
                {unavailable.length > 0 && (
                  <details>
                    <summary>{t`${unavailable.length} unavailable series — show source reasons`}</summary>
                    {unavailable.map((entry) => (
                      <Text key={entry.binding_id} size='sm'>
                        {entry.display_name || entry.external_key}:{' '}
                        {entry.detail || entry.reason || t`History unavailable`}
                      </Text>
                    ))}
                  </details>
                )}
                {empty.length > 0 && (
                  <Text
                    maw='70ch'
                    size='sm'
                  >{t`No samples in window: ${empty.map((entry) => entry.display_name || entry.external_key).join(', ')}. No samples is different from a recorded zero.`}</Text>
                )}
              </Alert>
            )}
            {!!response.read_batches?.some((batch) => batch.failed) && (
              <AttentionAlert title={t`Partial history read`}>
                {t`${response.read_batches.filter((batch) => batch.failed).length} of ${response.read_batches.length} bounded batches failed. Available charts remain visible; missing batches are not complete evidence.`}
                <Button
                  className={monitoring.link}
                  size='compact-xs'
                  variant='subtle'
                  onClick={onRetry}
                >{t`Retry history`}</Button>
              </AttentionAlert>
            )}
            {response.read_batches && response.read_batches.length > 1 && (
              <details>
                <summary>{t`Per-batch resolution and clock`}</summary>
                {response.read_batches.map((batch, i) => (
                  <Text
                    maw='70ch'
                    size='sm'
                    key={`${response.window_start}:${batch.bindings.join(',')}:${batch.keys.join(',')}`}
                  >
                    {t`Batch ${i + 1}`}:{' '}
                    {batch.failed
                      ? t`History read failed; coverage unknown.`
                      : t`Resolution ${batch.resolution_seconds ?? t`not supplied`} s; measured cadence ${batch.cadence_seconds ?? t`not supplied`} s; display shift ${batch.display_shift_seconds ?? t`not supplied`} s.`}
                  </Text>
                ))}
              </details>
            )}
            {response.read_batches && response.read_batches.length > 1 && (
              <MetaText
                maw='70ch'
                size='sm'
              >{t`${response.read_batches.length} bounded batches share these time bounds. Each may read overlapping source documents; batching does not imply flat source work or payload.`}</MetaText>
            )}
          </>
        )}
        <MetaText maw='70ch' size='sm'>
          {extent?.available && extent.from && extent.to
            ? t`Reported source extent: ${displayInstant(extent.from) ?? t`Time unavailable`} — ${displayInstant(extent.to) ?? t`Time unavailable`}. Availability within it is not guaranteed.`
            : t`Source extent unavailable. This does not prove that the source has no history.`}
        </MetaText>
        {failed && (
          <Group>
            <Button
              size='sm'
              variant='light'
              onClick={onRetry}
            >{t`Retry history`}</Button>
          </Group>
        )}
      </Stack>
    </Paper>
  );
}
