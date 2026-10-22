import type { MachineSignal } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { unitLabel } from '../performance/format';

/** Present only supplied values and units; keep the raw value in the reason. */
export function signalReading(signal: Pick<MachineSignal, 'value' | 'unit'>): {
  text: string;
  reason: string | null;
  kind: 'value' | 'missing' | 'invalid';
} {
  if (signal.value == null) {
    return { text: '—', reason: t`No observation available`, kind: 'missing' };
  }
  if (typeof signal.value === 'number' && !Number.isFinite(signal.value)) {
    return { text: '—', reason: t`Invalid observation value`, kind: 'invalid' };
  }
  const value =
    typeof signal.value === 'number'
      ? Number.parseFloat(signal.value.toFixed(3)).toString()
      : String(signal.value);
  if (!signal.unit?.trim()) {
    return {
      text: t`${value} (no unit)`,
      reason: null,
      kind: 'value'
    };
  }
  return {
    text:
      signal.unit.trim() === '1' ? value : `${value} ${unitLabel(signal.unit)}`,
    reason: null,
    kind: 'value'
  };
}
