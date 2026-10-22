import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Card,
  Divider,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { ValueTooltip } from '../../components/items/ValueTooltip';
import { Timestamp } from './health/Timestamp';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';
import { FaultOccurrences } from './faults/FaultOccurrences';
import { OccurrenceInvestigation } from './investigation/OccurrenceInvestigation';
import operations from './operations.module.css';

interface CountRow {
  code?: string;
  cause?: string;
  count: number;
}

interface FaultHistory {
  window_days: number;
  declared: { fault_codes: string[] };
  observed: {
    maintenance: {
      count: number;
      first_date: string | null;
      last_date: string | null;
      gap_days: {
        min: number | null;
        max: number | null;
        median: number | null;
      };
      repeat_window_flag: boolean;
      repeat_window_count: number;
    };
    failure_codes: { top: CountRow[] };
    verified_causes: { top: CountRow[] };
  };
}

export function faultDescription(code?: string): string {
  const suffix = code?.replace(/^CC-PUMP-/, '');
  return (
    (
      {
        LOW_FLOW: t`Low flow`,
        BEARING_VIBRATION: t`Bearing vibration`,
        OVER_TEMPERATURE: t`High temperature`,
        MOTOR_OVERLOAD: t`Motor overload`
      } as Record<string, string>
    )[suffix ?? ''] ??
    (code?.startsWith('CC-PUMP-')
      ? code
          .slice(8)
          .replaceAll('_', ' ')
          .toLowerCase()
          .replace(/^./, (letter) => letter.toUpperCase())
      : t`Unclassified fault`)
  );
}

function CountTable({
  title,
  source,
  rows,
  labelKey
}: Readonly<{
  title: string;
  source: string;
  rows: CountRow[];
  labelKey: 'code' | 'cause';
}>) {
  return (
    <Card withBorder padding='sm'>
      <Stack gap={4}>
        <Text fw={600} size='sm'>
          {title}
        </Text>

        {rows.length === 0 ? (
          <Text size='xs' className={operations.meta}>{t`No records`}</Text>
        ) : (
          <Table withRowBorders={false} verticalSpacing={2}>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr key={row[labelKey]}>
                  <Table.Td>
                    <ValueTooltip
                      label={
                        row[labelKey] ?? t`Fault classification not recorded`
                      }
                    >
                      <Text size='xs'>
                        {labelKey === 'code'
                          ? faultDescription(row.code)
                          : row.cause}
                      </Text>
                    </ValueTooltip>
                  </Table.Td>
                  <Table.Td width={60}>
                    <Badge variant='light' color='gray'>
                      {row.count}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Card>
  );
}

/**
 * R6 UI08 Fault History: occurrences and their retained evidence lead;
 * the native maintenance rollup follows. The two load
 * independently, so one failing never hides the other.
 */
export default function FaultHistoryPanel({
  machineId
}: Readonly<{ machineId: number }>) {
  return (
    <Stack gap='lg'>
      <FaultOccurrences
        machineId={machineId}
        renderInvestigation={(occurrence) => (
          <OccurrenceInvestigation
            machineId={machineId}
            occurrence={occurrence}
          />
        )}
      />
      <Divider label={t`Maintenance history`} labelPosition='left' />
      <FaultHistoryRollup machineId={machineId} />
    </Stack>
  );
}

/**
 * Deterministic fault-history rollup (C4): server-computed aggregates only —
 * maintenance cadence, approved-scope failure codes, and causes from
 * verified closeouts. Declared and observed provenance are labelled apart.
 */
function FaultHistoryRollup({ machineId }: Readonly<{ machineId: number }>) {
  const api = useApi();
  const query = useQuery({
    queryKey: ['machine-fault-history', machineId],
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.asset_machine_fault_history, machineId))
        .then((response) => response.data as FaultHistory)
  });

  if (query.isLoading) return <Loader size='sm' />;
  if (query.isError || !query.data) {
    return (
      <Alert color='red' icon={<IconAlertTriangle size={16} />}>
        {t`The fault history could not be loaded.`}
      </Alert>
    );
  }

  const data = query.data;
  const maintenance = data.observed.maintenance;

  return (
    <Stack gap='sm' data-testid='fault-history-panel'>
      <Group gap='xs'>
        <Text size='sm'>
          {maintenance.count} {t`maintenance records in the last`}{' '}
          {data.window_days} {t`days`}
        </Text>
        {maintenance.repeat_window_flag && (
          <Badge color='orange' variant='filled'>
            {t`Repeat activity`}: {maintenance.repeat_window_count} / 30d
          </Badge>
        )}
      </Group>
      {maintenance.last_date && (
        <Group gap={6}>
          <Text size='sm'>{t`Last maintenance`}</Text>
          <Timestamp value={maintenance.last_date} dateOnly />
        </Group>
      )}
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <CountTable
          title={t`Frequent faults`}
          source={t`From approved repair scopes`}
          rows={data.observed.failure_codes.top}
          labelKey='code'
        />
        <CountTable
          title={t`Verified causes`}
          source={t`From verified closeouts`}
          rows={data.observed.verified_causes.top}
          labelKey='cause'
        />
      </SimpleGrid>
    </Stack>
  );
}
