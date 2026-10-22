import type { MachineSignal } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { unitLabel } from '../performance/format';
import { resolveSignal } from '../performance/resolve';

/** Present only supplied values and units; keep the raw value in the reason. */
export function signalReading(
  signal: Pick<MachineSignal, 'value' | 'unit'> &
    Partial<Pick<MachineSignal, 'external_key' | 'display_name'>>
): {
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
  const role = signal.external_key
    ? resolveSignal(signal as MachineSignal).definition.role
    : '';
  if (['status', 'station_status', 'motor_status'].includes(role)) {
    const code = String(signal.value);
    const label =
      role === 'motor_status'
        ? ({ '0': t`Off`, '1': t`On` } as Record<string, string>)[code]
        : ({ I: t`Idle`, R: t`Running` } as Record<string, string>)[code];
    return {
      text: label ?? t`Unknown`,
      reason: t`Plant status code ${code}`,
      kind: 'value'
    };
  }
  const value =
    typeof signal.value === 'number'
      ? Number.parseFloat(
          signal.value.toFixed(role === 'forebay' ? 2 : 3)
        ).toString()
      : String(signal.value);
  if (!signal.unit?.trim()) {
    return {
      text: value,
      reason:
        role === 'power_factor' || role === 'pump_count'
          ? null
          : t`Unit not reported by the plant`,
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

/** Known short plant keys get the label from the reviewed parameter catalogue. */
export function signalLabel(signal: MachineSignal): string {
  if (
    /^(?:pc|dv|pmw|sl)$/i.test(signal.display_name?.trim() ?? '') ||
    !signal.display_name?.trim()
  ) {
    const resolved = resolveSignal(signal);
    if (resolved.definition.key !== 'OTHER') return resolved.label;
    if (/^\/(?:pd\/[^/]+\/)?pmw$/.test(signal.external_key))
      return t`Input power`;
  }
  return signal.display_name;
}
