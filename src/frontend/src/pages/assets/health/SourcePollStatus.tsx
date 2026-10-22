import type { HealthSourceStatus } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { displayInstant } from '../displayClock';
import { recordedTelemetryAsLive } from '../telemetryPresentation';
import { useNow } from '../useNow';
import { FreshnessText, MetaText, freshnessFacts } from './common';
/** O27 reports current observations; O23 retains the original poll facts. */
export function SourcePollStatus({
  source
}: Readonly<{ source: HealthSourceStatus }>) {
  const now = useNow();
  if (!source.active) return <MetaText>{t`Disabled`}</MetaText>;
  const errorAt = Date.parse(source.last_error_at ?? '');
  const successAt = Date.parse(source.last_success_at ?? '');
  const unresolvedFailure =
    source.read_failed ??
    (!source.healthy &&
      (Number.isFinite(errorAt)
        ? !Number.isFinite(successAt) || errorAt > successAt
        : !!source.last_error_code));
  if (
    recordedTelemetryAsLive(source) &&
    source.reporting != null &&
    !unresolvedFailure
  ) {
    return (
      <>
        <MetaText>
          {source.reporting ? t`Source reporting` : t`No current reading`}
        </MetaText>
        <FreshnessText
          preferJustNow
          observedAt={source.last_observed_at ?? null}
          stale={!source.reporting}
          thresholdSeconds={source.freshness_threshold_seconds}
          now={now}
        />
      </>
    );
  }
  const at = source.healthy ? source.last_success_at : source.last_error_at;
  if (!source.healthy && !displayInstant(at) && !source.last_error_code)
    return <MetaText>{t`Last poll result unavailable`}</MetaText>;
  const time = displayInstant(at) ?? t`time not supplied`;
  const fact = freshnessFacts(at, false, null, now);
  const age = fact
    ? fact.future
      ? t`${fact.age} ahead of your clock`
      : t`${fact.age} ago`
    : null;
  return (
    <MetaText>
      {source.healthy
        ? t`Succeeded ${time}${age ? ` (${age})` : ''}`
        : t`Failed ${time} · ${source.last_error_code || t`code not supplied`}`}
    </MetaText>
  );
}
