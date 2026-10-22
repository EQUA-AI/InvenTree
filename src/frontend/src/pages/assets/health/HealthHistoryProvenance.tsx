import type { SignalTrend } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { IconInfoCircle } from '@tabler/icons-react';
import { ValueTooltip } from '../../../components/items/ValueTooltip';

/** One optional history context tooltip; failures remain in the chart's status. */
export function HealthHistoryProvenance({
  trend,
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
  return (
    <ValueTooltip
      accessibleLabel={t`History information`}
      label={
        [
          trend?.source_name ?? targetSource,
          trend?.cadence_seconds
            ? t`Sample interval ${trend.cadence_seconds} s`
            : null,
          trend?.sampled ? t`Sampled history; gaps are not filled` : null
        ]
          .filter(Boolean)
          .join(' · ') || t`History source not supplied`
      }
    >
      <IconInfoCircle size={18} />
    </ValueTooltip>
  );
}
