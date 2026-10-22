import type { MachineComponent } from '@lib/types/Assets';
import { t } from '@lingui/core/macro';
import {
  MissingValue,
  ValueTooltip
} from '../../components/items/ValueTooltip';
import {
  formatInstant,
  useDisplayClock
} from '../../pages/assets/health/mimic/observedTime';
import classes from './MachineComponentTable.module.css';
import { componentSource } from './componentPresentation';

export function ComponentProvenance({
  record
}: Readonly<{ record: MachineComponent }>) {
  const clock = useDisplayClock();
  const source = componentSource(record);
  if (source.kind === 'missing')
    return <MissingValue reason={t`Source not recorded`} />;
  const label =
    source.kind === 'register'
      ? t`Registry import`
      : source.kind === 'scada'
        ? source.inferred
          ? t`Inferred SCADA`
          : t`SCADA mapping`
        : source.kind === 'manual'
          ? t`Manual entry`
          : source.label;
  const imported = formatInstant(source.importedAt, clock);
  const detail = [source.raw || label, imported, imported && clock.label]
    .filter(Boolean)
    .join(' · ');
  return (
    <ValueTooltip label={detail} className={classes.source} data-table-tooltip>
      <span className={classes.sourceText}>{label}</span>
    </ValueTooltip>
  );
}
