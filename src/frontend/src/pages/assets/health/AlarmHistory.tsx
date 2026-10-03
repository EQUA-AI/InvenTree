import { t } from '@lingui/core/macro';
import { Badge, Button, Group, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { MachineAnomaly } from '@lib/types/MachineHealth';
import { useApi } from '../../../contexts/ApiContext';
import { SeverityBadge } from './common';

/** How many past alarms are shown before the rest are asked for. */
const FIRST_PAGE = 10;

function when(value: string | null): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—';
}

/**
 * The alarms a machine has had and no longer has.
 *
 * Every resolved anomaly is kept, with who acknowledged it and what they
 * wrote, and how it ended - a reading back inside its limits, or the
 * detector no longer able to tell. Until now nothing listed them, so the
 * record existed and could not be read. This is the maintenance history an
 * audit asks for: what went wrong on this machine, when, who saw it, and
 * what became of it.
 */
export function AlarmHistory({ machineId }: Readonly<{ machineId: number }>) {
  const api = useApi();
  const [showAll, setShowAll] = useState(false);
  const query = useQuery<MachineAnomaly[]>({
    queryKey: ['machine-health-anomalies', machineId, 'resolved'],
    queryFn: async () => {
      const response = await api.get(
        apiUrl(ApiEndpoints.machine_health_anomalies, machineId),
        { params: { status: 'resolved' } }
      );
      return response.data?.results ?? [];
    }
  });

  const alarms = query.data ?? [];
  if (query.isLoading) {
    return <Text c='dimmed'>{t`Loading past alarms…`}</Text>;
  }
  if (!alarms.length) {
    return (
      <Text c='dimmed'>{t`No past alarms recorded for this machine.`}</Text>
    );
  }
  const shown = showAll ? alarms : alarms.slice(0, FIRST_PAGE);
  const hidden = alarms.length - shown.length;

  return (
    <Stack gap='xs'>
      <Table.ScrollContainer minWidth={900}>
        <Table striped data-testid='alarm-history'>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={120}>{t`Severity`}</Table.Th>
              <Table.Th>{t`Alarm`}</Table.Th>
              <Table.Th>{t`First observed`}</Table.Th>
              <Table.Th>{t`Resolved`}</Table.Th>
              <Table.Th>{t`How it ended`}</Table.Th>
              <Table.Th>{t`Acknowledged`}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shown.map((alarm) => (
              <Table.Tr key={alarm.pk} data-anomaly={alarm.pk}>
                <Table.Td>
                  <SeverityBadge severity={alarm.severity} />
                </Table.Td>
                <Table.Td>
                  <Text size='sm' fw={500}>
                    {alarm.title}
                  </Text>
                  {alarm.evidence_summary && (
                    <Text size='xs' c='dimmed'>
                      {alarm.evidence_summary}
                    </Text>
                  )}
                  {alarm.work_order && (
                    <Badge variant='outline' color='blue' size='xs' mt={4}>
                      {t`Repair raised`}
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>{when(alarm.first_observed_at)}</Table.Td>
                <Table.Td>{when(alarm.resolved_at)}</Table.Td>
                <Table.Td>
                  <Text size='sm'>{alarm.resolution_note || '—'}</Text>
                </Table.Td>
                <Table.Td>
                  {/* Who saw it, and what they wrote; an alarm nobody
                      acknowledged before it ended says so. */}
                  {alarm.acknowledged_by_name ? (
                    <>
                      <Text size='sm'>{alarm.acknowledged_by_name}</Text>
                      {alarm.acknowledgement_note && (
                        <Text size='xs' c='dimmed'>
                          {alarm.acknowledgement_note}
                        </Text>
                      )}
                    </>
                  ) : (
                    <Text size='sm' c='dimmed'>
                      {t`Not acknowledged`}
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {hidden > 0 && (
        <Group>
          <Button variant='subtle' size='xs' onClick={() => setShowAll(true)}>
            {t`Show ${hidden} more`}
          </Button>
        </Group>
      )}
    </Stack>
  );
}
