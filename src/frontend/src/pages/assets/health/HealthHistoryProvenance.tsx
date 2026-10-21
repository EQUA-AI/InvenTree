import type { SignalTrend } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { Spoiler, Stack, Text } from '@mantine/core';
import { displayInstant } from '../displayClock';
import monitoring from '../monitoring.module.css';
import { MetaText } from './common';

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
      <MetaText maw='70ch'>
        {t`Source`}: {trend?.source_name ?? targetSource ?? t`Not mapped`} ·{' '}
        {window || trend
          ? `${Math.round((Date.parse(window?.to ?? trend!.window_end) - Date.parse(window?.from ?? trend!.window_start)) / 60000)} min`
          : t`Window not read`}{' '}
        · {cadence ? t`measured ${cadence} s` : t`interval unavailable`} ·{' '}
        {t`gaps not filled`}
      </MetaText>
      <Spoiler
        classNames={{ control: monitoring.link }}
        maxHeight={0}
        showLabel={t`Show read details`}
        hideLabel={t`Hide read details`}
      >
        <Text maw='70ch' size='xs'>
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
          <Text maw='70ch' size='xs'>
            {t`Requested window`}:{' '}
            {displayInstant(window.from) ?? t`Time unavailable`} —{' '}
            {displayInstant(window.to) ?? t`Time unavailable`}.
          </Text>
        )}
        {!trend && (
          <Text maw='70ch' size='xs'>
            {t`Target source`}: {targetSource ?? t`Not mapped`} ·{' '}
            {t`Request budget: 4320 samples; at most 6 h per read.`}
          </Text>
        )}
        {trend && (
          <>
            <Text maw='70ch' size='xs'>
              {t`History source`}: {trend.source_name} · {t`Samples requested`}:{' '}
              {trend.max_samples} · {t`Most samples per read`}:{' '}
              {trend.limits.max_samples} · {t`Window limit`}:{' '}
              {trend.limits.max_window_seconds / 3600} h.
            </Text>
            <Text maw='70ch' size='xs'>
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
      </Spoiler>
    </Stack>
  );
}
