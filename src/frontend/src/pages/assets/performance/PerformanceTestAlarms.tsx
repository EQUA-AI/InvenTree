import type { MachineSignal } from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import { Alert, Stack, Text, useComputedColorScheme } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { type ReactNode, createContext, useContext, useMemo } from 'react';
import { formatInstant, formatValue } from './format';
import { resolveSignal } from './resolve';
import { type PerformanceTestAlarm, testAlarmRows } from './testAlarmRows';

const TestAlarmContext = createContext<PerformanceTestAlarm[]>([]);

export function PerformanceTestAlarmProvider({
  signals,
  children
}: Readonly<{
  signals: MachineSignal[];
  children: ReactNode;
}>) {
  const alarms = useMemo(
    () =>
      testAlarmRows(
        signals,
        new Map(
          signals.map((signal) => [
            signal.binding_id,
            resolveSignal(signal).label
          ])
        )
      ),
    [signals]
  );
  return (
    <TestAlarmContext.Provider value={alarms}>
      {children}
    </TestAlarmContext.Provider>
  );
}

export function usePerformanceTestAlarms(bindingIds?: readonly number[]) {
  const alarms = useContext(TestAlarmContext);
  const key = bindingIds ? bindingIds.join(',') : null;
  return useMemo(() => {
    if (key === null) return alarms;
    const ids = new Set(key.split(',').map(Number));
    return alarms.filter((alarm) => ids.has(alarm.binding_id));
  }, [alarms, key]);
}

export function PerformanceTestNotice({
  bindingIds,
  historyAvailable,
  afterHistory = false,
  primary = false
}: Readonly<{
  bindingIds?: readonly number[];
  historyAvailable?: boolean;
  afterHistory?: boolean;
  primary?: boolean;
}>) {
  const alarms = usePerformanceTestAlarms(bindingIds);
  const scheme = useComputedColorScheme('light');
  if (!alarms.length) return null;
  return (
    <Alert
      role={primary ? 'alert' : 'status'}
      aria-live={primary ? 'assertive' : 'polite'}
      color={scheme === 'dark' ? 'red.3' : 'red.9'}
      variant='outline'
      icon={<IconAlertTriangle size={18} />}
      title={t`TEST alarm — Critical`}
      data-testid='performance-test-alarm'
    >
      <Stack gap={4}>
        <Text size='sm'>{t`Synthetic exercise. Test values are separate from measured telemetry and historical readings.`}</Text>
        {afterHistory && (
          <Text size='sm'>{t`Exercise began after this history window. Refresh or return to Live to see the TEST trace.`}</Text>
        )}
        {historyAvailable === false && (
          <Text size='sm'>{t`No measured history in this window. Any TEST trace is synthetic and begins only when the exercise was triggered.`}</Text>
        )}
        {alarms.map((alarm) => (
          <Stack key={alarm.anomaly_id} gap={2}>
            <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
              {primary
                ? t`${alarm.label}: test ${formatValue(alarm.test_value, 1, alarm.unit)}`
                : t`${alarm.label}: test ${formatValue(alarm.test_value, 1, alarm.unit)}; measured ${typeof alarm.measured_value === 'number' && !alarm.measured_stale && alarm.measured_quality === 'good' ? formatValue(alarm.measured_value, 1, alarm.measured_unit) : '—'}`}
            </Text>
            <Text size='xs'>
              {alarm.status === 'acknowledged' ? t`Acknowledged` : t`Open`}
              {' · '}
              {formatInstant(Date.parse(alarm.triggered_at))}
            </Text>
            {!primary && alarm.measured_stale && (
              <Text size='xs'>{t`Measured observation is stale.`}</Text>
            )}
            {!primary && alarm.measured_quality !== 'good' && (
              <Text size='xs'>{t`Measured observation has bad or unconfirmed quality.`}</Text>
            )}
            {alarm.unitMismatch && (
              <Text size='xs'>{t`Units differ. The test value is not plotted on the measured axis.`}</Text>
            )}
          </Stack>
        ))}
      </Stack>
    </Alert>
  );
}
