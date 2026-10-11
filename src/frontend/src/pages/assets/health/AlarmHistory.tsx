import { t } from '@lingui/core/macro';
import {
  Anchor,
  Button,
  Group,
  SegmentedControl,
  Stack,
  Table,
  Text,
  TextInput
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { MachineAnomaly } from '@lib/types/MachineHealth';
import { useApi } from '../../../contexts/ApiContext';
import { SeverityBadge } from './common';

/** How many past alarms are shown before the rest are asked for. */
const FIRST_PAGE = 10;

type Dismissal = { by?: string; note?: string };

function when(value: string | null): string {
  return value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—';
}

/** Everything about an alarm a reader might search the history for. */
function haystack(alarm: MachineAnomaly): string {
  const dismissed = alarm.metrics?.dismissed as Dismissal | undefined;
  return [
    alarm.title,
    alarm.evidence_summary,
    alarm.resolution_note,
    alarm.acknowledged_by_name,
    alarm.acknowledgement_note,
    dismissed?.by,
    dismissed?.note
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

/**
 * The alarms a machine has had and no longer has.
 *
 * Every resolved anomaly is kept, with who acknowledged or dismissed it and
 * what they wrote, and how it ended - a reading back inside its limits, or
 * the detector no longer able to tell. This is the maintenance history an
 * audit asks for: what went wrong on this machine, when, who saw it, and
 * what became of it.
 *
 * Both times shown are when the condition was observed, first and last, and
 * both are on the same clock - the one the rest of the page shows. When the
 * detector wrote the row closed is a fact about this server, on another
 * clock, and putting it beside them made an alarm on replayed history look
 * as though it had ended before it began.
 */
export function AlarmHistory({ machineId }: Readonly<{ machineId: number }>) {
  const api = useApi();
  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState('');
  const [severity, setSeverity] = useState('all');
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
  const wanted = search.trim().toLowerCase();
  const matching = alarms.filter(
    (alarm) =>
      (severity === 'all' || alarm.severity === severity) &&
      (!wanted || haystack(alarm).includes(wanted))
  );
  const shown = showAll ? matching : matching.slice(0, FIRST_PAGE);
  const hidden = matching.length - shown.length;
  const total = alarms.length;
  const count = matching.length;

  return (
    <Stack gap='xs'>
      <Group gap='sm'>
        <TextInput
          size='xs'
          w={260}
          aria-label={t`Search past alarms`}
          placeholder={t`Search alarm, note or name`}
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
        />
        <SegmentedControl
          size='xs'
          aria-label={t`Severity`}
          value={severity}
          onChange={setSeverity}
          data={[
            { value: 'all', label: t`All` },
            { value: 'critical', label: t`Critical` },
            { value: 'warning', label: t`Warning` }
          ]}
        />
        <Text size='xs' c='dimmed'>
          {count === total
            ? t`${total} alarms`
            : t`${count} of ${total} alarms`}
        </Text>
      </Group>
      <Table.ScrollContainer minWidth={900}>
        <Table striped data-testid='alarm-history'>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={120}>{t`Severity`}</Table.Th>
              <Table.Th>{t`Alarm`}</Table.Th>
              <Table.Th>{t`First observed`}</Table.Th>
              <Table.Th>{t`Last observed`}</Table.Th>
              <Table.Th>{t`How it ended`}</Table.Th>
              <Table.Th>{t`Seen by`}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {shown.map((alarm) => {
              const dismissed = alarm.metrics?.dismissed as
                | Dismissal
                | undefined;
              return (
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
                      <Anchor
                        component={Link}
                        to={`/maintenance/work-orders/${alarm.work_order}/`}
                        size='xs'
                      >
                        {t`Open the repair raised for it`}
                      </Anchor>
                    )}
                  </Table.Td>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>
                    {when(alarm.first_observed_at)}
                  </Table.Td>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>
                    {when(alarm.last_observed_at)}
                  </Table.Td>
                  <Table.Td>
                    <Text size='sm'>{alarm.resolution_note || '—'}</Text>
                  </Table.Td>
                  <Table.Td>
                    {/* Who saw it, and what they wrote; an alarm nobody
                        touched before it ended says so. */}
                    {dismissed ? (
                      <>
                        <Text size='sm'>
                          {t`Dismissed by`} {dismissed.by || t`Unknown`}
                        </Text>
                        <Text size='xs' c='dimmed'>
                          {dismissed.note}
                        </Text>
                      </>
                    ) : alarm.acknowledged_by_name ? (
                      <>
                        <Text size='sm'>
                          {t`Acknowledged by`} {alarm.acknowledged_by_name}
                        </Text>
                        {alarm.acknowledgement_note && (
                          <Text size='xs' c='dimmed'>
                            {alarm.acknowledgement_note}
                          </Text>
                        )}
                      </>
                    ) : (
                      <Text size='sm' c='dimmed'>
                        {t`Nobody, before it ended`}
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {!count && <Text c='dimmed' size='sm'>{t`No past alarm matches.`}</Text>}
      {hidden > 0 && (
        <Group>
          <Button variant='subtle' size='xs' onClick={() => setShowAll(true)}>
            {t`Show ${hidden} more`}
          </Button>
        </Group>
      )}
      {alarms.some((alarm) => alarm.display_shifted) && (
        <Text size='xs' c='dimmed'>
          {t`Times are on the clock this station is presented on, the same as the rest of the page.`}
        </Text>
      )}
    </Stack>
  );
}
