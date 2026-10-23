import type { MachineSignal } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  FailedValue,
  MissingValue,
  ValueTooltip
} from '../../../components/items/ValueTooltip';
import { signalReading } from './readingPresentation';

export function SignalValue({
  signal,
  focusable = true
}: Readonly<{
  signal: Pick<MachineSignal, 'value' | 'unit' | 'stale'> &
    Partial<Pick<MachineSignal, 'quality' | 'external_key' | 'display_name'>>;
  focusable?: boolean;
}>) {
  const reading = signalReading(signal);
  if (reading.kind === 'invalid' || signal.quality === 'bad')
    return (
      <FailedValue
        label={t`Value`}
        reason={
          signal.quality === 'bad'
            ? t`Bad quality reported by the source`
            : reading.reason!
        }
        value={reading.kind === 'value' ? reading.text : undefined}
        focusable={focusable}
      />
    );
  if (reading.kind === 'missing')
    return (
      <MissingValue
        label={t`Value`}
        reason={reading.reason!}
        focusable={focusable}
      />
    );
  const value = signal.stale ? t`Last known ${reading.text}` : reading.text;
  return reading.reason ? (
    <ValueTooltip label={reading.reason} focusable={focusable}>
      {value}
    </ValueTooltip>
  ) : (
    <>{value}</>
  );
}
