import type { MachineComponent } from '@lib/types/Assets';
import { i18n } from '@lingui/core';
import { t } from '@lingui/core/macro';
import { Text, Tooltip } from '@mantine/core';
import classes from './MachineComponentTable.module.css';
import { componentSource } from './componentPresentation';

export function MissingPartFact({ reason }: Readonly<{ reason: string }>) {
  return (
    <Tooltip
      label={reason}
      events={{ hover: true, focus: true, touch: true }}
      multiline
      w={260}
    >
      <Text
        component='span'
        role='img'
        tabIndex={0}
        aria-label={reason}
        data-table-tooltip
        size='sm'
        className={classes.missing}
      >
        —
      </Text>
    </Tooltip>
  );
}

export function ComponentProvenance({
  record
}: Readonly<{ record: MachineComponent }>) {
  const source = componentSource(record);
  if (!source.label) return <MissingPartFact reason={t`Source not recorded`} />;
  const label =
    source.label === 'Equipment register import'
      ? t`Equipment register import`
      : source.label === 'SCADA tag mapping'
        ? t`SCADA tag mapping`
        : source.label;
  return (
    <Tooltip
      label={source.raw || label}
      events={{ hover: true, focus: true, touch: true }}
      multiline
      w={280}
    >
      <Text
        component='span'
        size='sm'
        tabIndex={0}
        data-table-tooltip
        className={classes.source}
      >
        {label}
        {source.importedAt && (
          <>
            {' '}
            ·{' '}
            {new Intl.DateTimeFormat(i18n.locale || 'en', {
              day: 'numeric',
              month: 'short',
              year: 'numeric',
              timeZone: 'UTC'
            }).format(new Date(source.importedAt))}
          </>
        )}
      </Text>
    </Tooltip>
  );
}
