import type { HealthSourceStatus } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  FailedValue,
  MissingValue
} from '../../../components/items/ValueTooltip';
import { recordedTelemetryAsLive } from '../telemetryPresentation';
import { MetaText } from './common';
/** A source state; its observation/error times belong in the adjacent columns. */
export function SourcePollStatus({
  source
}: Readonly<{ source: HealthSourceStatus }>) {
  if (!source.active) return <MetaText>{t`Disabled`}</MetaText>;
  const errorAt = Date.parse(source.last_error_at ?? '');
  const successAt = Date.parse(source.last_success_at ?? '');
  const failed =
    source.read_failed ??
    (!source.healthy &&
      (Number.isFinite(errorAt)
        ? !Number.isFinite(successAt) || errorAt > successAt
        : !!source.last_error_code));
  if (failed) return <FailedValue reason={t`The source could not be read`} />;
  if (recordedTelemetryAsLive(source) && source.reporting != null)
    return (
      <MetaText>
        {source.reporting ? t`Reporting` : t`No current reading`}
      </MetaText>
    );
  return source.healthy ? (
    <MetaText>{t`Reporting`}</MetaText>
  ) : (
    <MissingValue reason={t`Source status not reported`} />
  );
}
