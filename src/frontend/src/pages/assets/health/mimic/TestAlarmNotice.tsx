import { t } from '@lingui/core/macro';
import { Alert, Stack, Text, useComputedColorScheme } from '@mantine/core';
import { valueText } from './format';
import type { MimicAlarm } from './types';

export function TestAlarmNotice({
  alarms
}: Readonly<{ alarms: MimicAlarm[] }>) {
  const scheme = useComputedColorScheme('light');
  return alarms.some(
    (alarm) =>
      alarm.synthetic &&
      ['open', 'acknowledged'].includes(alarm.anomaly_status ?? '')
  ) ? (
    <Alert
      color={scheme === 'dark' ? 'red.3' : 'red.9'}
      title={t`Synthetic test alarm active`}
    >
      <Stack gap={4}>
        {t`Alarm highlighting includes a test exercise. Actual measurements and operational authority are unchanged.`}
        {alarms
          .filter(
            (alarm) =>
              alarm.synthetic &&
              ['open', 'acknowledged'].includes(alarm.anomaly_status ?? '')
          )
          .map((alarm) => (
            <Text key={alarm.anomaly} size='sm' fw={600}>
              {t`TEST`} {valueText(alarm)} · {alarm.label}
            </Text>
          ))}
      </Stack>
    </Alert>
  ) : null;
}
