import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Card,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Table,
  Text
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';

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
        <Text size='xs' c='dimmed'>
          {source}
        </Text>
        {rows.length === 0 ? (
          <Text size='xs' c='dimmed'>{t`No records`}</Text>
        ) : (
          <Table withRowBorders={false} verticalSpacing={2}>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr key={row[labelKey]}>
                  <Table.Td>
                    <Text size='xs'>{row[labelKey]}</Text>
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
 * Deterministic fault-history rollup (C4): server-computed aggregates only —
 * maintenance cadence, approved-scope failure codes, and causes from
 * verified closeouts. Declared and observed provenance are labelled apart.
 */
export default function FaultHistoryPanel({
  machineId
}: Readonly<{ machineId: number }>) {
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
      {maintenance.count > 0 && (
        <Text size='xs' c='dimmed'>
          {t`First`} {maintenance.first_date} — {t`last`}{' '}
          {maintenance.last_date}
          {maintenance.gap_days.median != null && (
            <>
              {' - '}
              {t`gap days (min/median/max)`}: {maintenance.gap_days.min}/
              {maintenance.gap_days.median}/{maintenance.gap_days.max}
            </>
          )}
        </Text>
      )}
      {data.declared.fault_codes.length > 0 && (
        <Group gap={4}>
          <Text size='xs' c='dimmed'>
            {t`Declared fault codes`}:
          </Text>
          {data.declared.fault_codes.map((code) => (
            <Badge key={code} variant='outline' color='gray'>
              {code}
            </Badge>
          ))}
        </Group>
      )}
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <CountTable
          title={t`Top failure codes`}
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
