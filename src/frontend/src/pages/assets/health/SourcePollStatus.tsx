import type { HealthSourceStatus } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { displayInstant } from '../displayClock';
import { useNow } from '../useNow';
import { MetaText, freshnessFacts } from './common';
/** Reports the last poll result, never a live link or communication verdict. */
export function SourcePollStatus({
  source
}: Readonly<{ source: HealthSourceStatus }>) {
  const now = useNow();
  if (!source.active) return <MetaText>{t`Disabled`}</MetaText>;
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
