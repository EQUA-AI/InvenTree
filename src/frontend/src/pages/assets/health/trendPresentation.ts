import type { SignalTrend } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { monitoringTerms } from '../monitoringTerms';
import { type PreparedTrend, prepareTrend } from './trendSamples';

export interface TrendStatusFact {
  failed: boolean;
  label: string;
  detail: string;
}

/** The same absent/failed history fact belongs to the cell and its row-info control. */
export function trendStatusFact(
  trend: SignalTrend | null | undefined,
  {
    pending = false,
    failed = false,
    prepared
  }: {
    pending?: boolean;
    failed?: boolean;
    prepared?: PreparedTrend;
  } = {}
): TrendStatusFact | null {
  if (pending) return null;
  if (failed)
    return {
      failed: true,
      label: t`Read failed`,
      detail: t`The trend read failed. The current reading is unaffected.`
    };
  if (!trend)
    return {
      failed: false,
      label: t`Not returned`,
      detail: t`The completed trend read did not return this signal. Its history is unknown; the current reading is unaffected.`
    };
  if (!trend.available) {
    const failed =
      trend.reason === 'SOURCE_UNAVAILABLE' ||
      trend.reason === 'SOURCE_TIMEOUT';
    return {
      failed,
      label: failed
        ? t`Read failed`
        : trend.reason === 'HISTORY_UNSUPPORTED'
          ? t`No history`
          : t`History unavailable`,
      detail:
        trend.detail?.trim() ||
        t`The source could not serve history for this signal.`
    };
  }
  const samples = prepared ?? prepareTrend(trend);
  if (samples.measured === 0 && samples.undated === 0)
    return {
      failed: false,
      label: monitoringTerms.noSamples(),
      detail: t`${trend.source_name} answered for this window but held no readings for this signal.`
    };
  if (samples.drawn < 2)
    return {
      failed: false,
      label:
        samples.drawn === 0
          ? t`No drawable readings`
          : samples.measured > 1
            ? t`1 of ${samples.measured} drawable`
            : t`Only one reading`,
      detail: t`${samples.drawn} of ${samples.measured} timed readings in this window can be drawn; ${samples.undated} readings have no readable time.`
    };
  return null;
}
