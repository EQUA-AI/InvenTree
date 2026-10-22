import { t } from '@lingui/core/macro';
import { Group, Text } from '@mantine/core';
import {
  MissingValue,
  ValueTooltip
} from '../../../../components/items/ValueTooltip';
import type { MimicPoint } from './types';

/** Selected-unit flags and the explicitly station-wide API count have different scopes. */
export function unconfiguredCount(points: MimicPoint[]): number | null {
  return points.length &&
    points.every((point) => typeof point.thresholds_configured === 'boolean')
    ? points.filter((point) => !point.thresholds_configured).length
    : null;
}

export function MimicAlarmCoverage({
  count,
  knownEmpty,
  station = false
}: Readonly<{
  count: number | null | undefined;
  knownEmpty: boolean;
  station?: boolean;
}>) {
  const supplied =
    typeof count === 'number' && Number.isInteger(count) && count >= 0;
  return (
    <Group gap={6} data-limit-coverage>
      {knownEmpty && <Text size='xs'>{t`No active alarms`} ·</Text>}
      {supplied ? (
        <ValueTooltip
          focusable={false}
          label={
            station
              ? t`Station readings without configured limits`
              : t`Selected pump readings without configured limits`
          }
        >
          <Text
            component='span'
            size='xs'
          >{t`${count} signals without limits`}</Text>
        </ValueTooltip>
      ) : (
        <>
          <Text size='xs'>{t`Limit coverage`}</Text>
          <MissingValue reason={t`Limit coverage not supplied`} />
        </>
      )}
    </Group>
  );
}
