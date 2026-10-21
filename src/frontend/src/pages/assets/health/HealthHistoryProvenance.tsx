import type { SignalTrend } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { Stack, Text } from '@mantine/core';

/** Native trend metadata, without inventing SeriesResponse resolution/extent. */
export function HealthHistoryProvenance({
  trend,
  loading,
  failed,
  fixed,
  blocked,
  window,
  targetSource
}: Readonly<{
  trend?: SignalTrend;
  loading: boolean;
  failed: boolean;
  fixed: boolean;
  blocked: boolean;
  window: { from: string; to: string } | null;
  targetSource?: string;
}>) {
  const cadence = trend?.cadence_seconds;
  return (
    <Stack gap={2} data-testid='health-history-provenance'>
      <Text
        size='xs'
        c='dimmed'
      >{t`Current condition, values and row sparklines use current observations and the recent 10-minute source window. They are not the selected historical range. Retained snapshots keep their capture-time origin.`}</Text>
      <Text size='xs'>
        {blocked
          ? t`Investigation window not read.`
          : fixed
            ? t`Chart origin: selected fixed source window.`
            : t`Chart origin: recent source window.`}{' '}
        {loading
          ? t`Reading source history…`
          : failed
            ? t`History read failed; an empty record is not established.`
            : !trend
              ? t`No source window has been read.`
              : !trend.available
                ? t`Source history unavailable for this window.`
                : t`Source window returned.`}
      </Text>
      {window && (
        <Text size='xs'>
          {t`Requested window`}:{' '}
          {new Date(window.from).toLocaleString(undefined, {
            timeZoneName: 'short'
          })}{' '}
          —{' '}
          {new Date(window.to).toLocaleString(undefined, {
            timeZoneName: 'short'
          })}
          .
        </Text>
      )}
      {!trend && (
        <Text size='xs'>
          {t`Target source`}: {targetSource ?? t`Not mapped`} ·{' '}
          {t`Request budget: 4320 samples; at most 6 h per read.`}
        </Text>
      )}
      {trend && (
        <>
          <Text size='xs'>
            {t`History source`}: {trend.source_name} · {t`Read budget`}:{' '}
            {trend.max_samples} · {t`Sample ceiling`}:{' '}
            {trend.limits.max_samples} · {t`Window limit`}:{' '}
            {trend.limits.max_window_seconds / 3600} h.
          </Text>
          <Text size='xs'>
            {cadence != null && cadence > 0
              ? t`Measured interval: ${cadence} s (estimate from this read).`
              : t`Measured interval unavailable.`}{' '}
            {trend.limits.expected_sample_interval_seconds != null
              ? t`Expected interval: ${trend.limits.expected_sample_interval_seconds} s (server default).`
              : t`Expected interval not supplied.`}{' '}
            {t`No resampling resolution or retention completeness is supplied by this endpoint. Fast transients may be absent; gaps are not filled.`}
          </Text>
        </>
      )}
    </Stack>
  );
}
