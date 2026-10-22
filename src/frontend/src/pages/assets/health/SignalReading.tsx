import type { MachineSignal } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  FailedValue,
  MissingValue
} from '../../../components/items/ValueTooltip';
import { signalReading } from './readingPresentation';

export function SignalValue({
  signal,
  focusable = true
}: Readonly<{
  signal: Pick<MachineSignal, 'value' | 'unit' | 'stale'> &
    Partial<Pick<MachineSignal, 'quality'>>;
  focusable?: boolean;
}>) {
  const reading = signalReading(signal);
  if (reading.kind === 'invalid' || signal.quality === 'bad')
    return (
      <FailedValue
        label={t`Value`}
        reason={reading.reason ?? t`Bad quality reported by the source`}
        value={reading.kind === 'value' ? reading.text : undefined}
        focusable={focusable}
      />
    );
  if (reading.reason)
    return (
      <MissingValue
        label={t`Value`}
        reason={reading.reason}
        focusable={focusable}
      />
    );
  return <>{signal.stale ? t`Last known ${reading.text}` : reading.text}</>;
}
