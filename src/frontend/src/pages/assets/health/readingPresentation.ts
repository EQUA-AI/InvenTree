import type { MachineSignal } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { unitLabel } from '../performance/format';

/** Present only supplied values and units; keep the raw value in the reason. */
export function signalReading(signal: Pick<MachineSignal, 'value' | 'unit'>): {
  text: string;
  reason: string | null;
} {
  if (signal.value == null) {
    return { text: '—', reason: t`No observation reported by the plant` };
  }
  if (typeof signal.value === 'number' && !Number.isFinite(signal.value)) {
    return { text: '—', reason: t`Invalid value reported by the plant` };
  }
  const value =
    typeof signal.value === 'number'
      ? Number.parseFloat(signal.value.toFixed(3)).toString()
      : String(signal.value);
  if (!signal.unit?.trim()) {
    return {
      text: '—',
      reason: t`Unit not reported by the plant; raw value ${value}`
    };
  }
  return { text: `${value} ${unitLabel(signal.unit)}`, reason: null };
}
