import { Badge } from '@mantine/core';
import { ValueTooltip } from '../../components/items/ValueTooltip';
import {
  formatInstant,
  useDisplayClock
} from '../../pages/assets/health/mimic/observedTime';
import classes from './MachineComponentTable.module.css';
import {
  type ComponentBasisRecord,
  componentBasis,
  componentReview
} from './componentPresentation';

export function ComponentBasis({
  record
}: Readonly<{ record: ComponentBasisRecord }>) {
  const basis = componentBasis(record);
  const clock = useDisplayClock();
  const imported = formatInstant(basis.importedAt, clock, { dateOnly: true });
  const detail = [basis.reason, imported, componentReview(record.review_note)]
    .filter(Boolean)
    .join(' · ');
  return (
    <ValueTooltip
      label={detail}
      accessibleLabel={`${basis.label}: ${detail}`}
      className={classes.fitTarget}
      data-table-tooltip
    >
      <Badge
        component='span'
        className={classes.fit}
        data-verified={record.status === 'verified'}
        size='sm'
        variant='outline'
        color={record.status === 'verified' ? 'green' : 'gray'}
        tt='none'
        h='auto'
        py={2}
        styles={{
          label: {
            whiteSpace: 'normal',
            overflow: 'visible',
            textOverflow: 'clip',
            lineHeight: 1.35
          }
        }}
      >
        {basis.label}
      </Badge>
    </ValueTooltip>
  );
}
