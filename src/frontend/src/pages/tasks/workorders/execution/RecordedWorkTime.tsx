import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import type { TimeRecords, TimeResult } from '@lib/types/RecordedFacts';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Accordion,
  Button,
  Group,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useState } from 'react';
import { RecordCommandState } from '../../../../components/records/RecordCommandState';
import { useRecordedCommand } from '../../../../components/records/useRecordedCommand';
import { useUserState } from '../../../../states/UserState';
import { recordedTime } from './ExecutionFacts';

export function workTimeEndpoint(id: number) {
  return `${apiUrl(ApiEndpoints.work_order_list)}${id}/time/`;
}
export function RecordedWorkTime({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const user = useUserState();
  const [saved, setSaved] = useState<TimeResult | null>(null);
  const source = workOrder.time_assignment?.time_records;
  const value: TimeRecords | undefined =
    saved && saved.version >= workOrder.lifecycle_version ? saved.time : source;
  const version = Math.max(saved?.version ?? 0, workOrder.lifecycle_version);
  const command = useRecordedCommand<TimeResult>(
    workTimeEndpoint(workOrder.id),
    setSaved,
    [['work-order']],
    'time'
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [reason, setReason] = useState('');
  const current = value?.intervals.filter((row) => !row.superseded) ?? [];
  const ownOpen = current.find(
    (row) => row.person_id === user.user?.pk && !row.ended_at
  );
  const locked = command.pending || !!command.retained;
  return (
    <Stack gap='sm' data-testid='recorded-work-time'>
      {value?.intervals.length ? (
        <>
          <Text fw={600} size='sm'>
            {value.recorded_seconds === null ||
            !Number.isFinite(value.recorded_seconds)
              ? t`Recorded person-work: not recorded · coverage unknown`
              : t`Recorded person-work: ${Math.floor(value.recorded_seconds / 60)} min at last server read · coverage partial`}
          </Text>
          {value.intervals.map((row) => (
            <Paper key={row.id} withBorder p='sm' radius='sm'>
              <Text size='sm' fw={600}>
                {row.person ?? t`Person not recorded`} ·{' '}
                {row.superseded
                  ? t`Superseded original`
                  : row.ended_at
                    ? t`Closed interval`
                    : value.open_at_closure
                      ? t`Open interval — requires closure review`
                      : t`Working — open interval`}
              </Text>
              <Text size='sm'>
                {recordedTime(row.started_at)} →{' '}
                {row.ended_at
                  ? recordedTime(row.ended_at)
                  : t`Open at last server read`}
              </Text>
              <Text size='xs' c='dimmed'>
                {t`Recorded by`}: {row.recorded_by ?? t`Not recorded`} ·{' '}
                {recordedTime(row.recorded_at)}
              </Text>
              {row.ended_at && (
                <>
                  <Text size='xs' c='dimmed'>
                    {t`End record`}:{' '}
                    {row.supersedes
                      ? t`Supervisor correction`
                      : row.end_action === 'pause'
                        ? t`Pause`
                        : row.end_action === 'stop'
                          ? t`Stop`
                          : t`Not recorded`}
                  </Text>
                  <Text size='xs' c='dimmed'>
                    {t`Ended by`}: {row.ended_by ?? t`Not recorded`} ·{' '}
                    {recordedTime(row.ended_recorded_at)}
                  </Text>
                </>
              )}
              {row.reason && <Text size='sm'>{row.reason}</Text>}
            </Paper>
          ))}
          <Accordion variant='contained' keepMounted={false}>
            <Accordion.Item value='gaps'>
              <Accordion.Control>{t`Unclassified gaps (${value.gaps.length})`}</Accordion.Control>
              <Accordion.Panel>
                <Stack gap='xs'>
                  {value.gaps.map((gap, index) => (
                    <Text size='sm' key={`${gap.person}-${index}`}>
                      {gap.person ?? t`Person not recorded`}:{' '}
                      {recordedTime(gap.started_at)} →{' '}
                      {recordedTime(gap.ended_at)} · {t`Unclassified`}
                    </Text>
                  ))}
                  <Text size='sm'>{t`Other people's coverage remains unknown. Gaps are not waiting or labor.`}</Text>
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>
        </>
      ) : (
        <>
          <Text
            fw={600}
            size='sm'
          >{t`Attributed time: unavailable · coverage unknown`}</Text>
          <Text size='sm'>{t`No person-work interval records are supplied. Unclassified gaps and labor totals cannot be measured. Held work does not mean every person was waiting; capture duration and browser activity do not classify labor.`}</Text>
        </>
      )}
      {value?.open_at_closure && (
        <Text
          c='orange'
          fw={600}
          size='sm'
        >{t`Work is closed with an open interval. Time stops at recorded closure; a supervisor must correct the interval. Missing closure time leaves the total unknown.`}</Text>
      )}
      {user.hasChangeRole(UserRoles.work_order) && (
        <Group
          data-testid='work-time-controls'
          grow
          preventGrowOverflow={false}
          style={{
            scrollMarginBottom:
              'calc(var(--aimms-work-dock-height, 96px) + env(safe-area-inset-bottom))'
          }}
        >
          <Button
            disabled={
              locked ||
              !!ownOpen ||
              ['completed', 'canceled'].includes(workOrder.lifecycle_status)
            }
            onClick={() => command.submit('start', {}, version)}
          >{t`Start work`}</Button>
          <Button
            variant='default'
            disabled={
              locked ||
              !ownOpen ||
              ['completed', 'canceled'].includes(workOrder.lifecycle_status)
            }
            onClick={() => command.submit('pause', {}, version)}
          >{t`Pause work`}</Button>
          <Button
            variant='default'
            disabled={
              locked ||
              !ownOpen ||
              ['completed', 'canceled'].includes(workOrder.lifecycle_status)
            }
            onClick={() => command.submit('stop', {}, version)}
          >{t`Stop work`}</Button>
        </Group>
      )}
      <Text
        size='xs'
        c='dimmed'
      >{t`Records your work interval using the server clock. These controls do not operate the machine. No browser activity is counted.`}</Text>
      {user.hasChangeRole(UserRoles.admin) && current.length > 0 && (
        <Accordion keepMounted={false}>
          <Accordion.Item value='correct'>
            <Accordion.Control>{t`Supervisor correction`}</Accordion.Control>
            <Accordion.Panel>
              <Stack gap='sm'>
                <Select
                  label={t`Interval to correct`}
                  value={selected}
                  onChange={(id) => {
                    setSelected(id);
                    const row = current.find((r) => String(r.id) === id);
                    setStart(row?.started_at ?? '');
                    setEnd(row?.ended_at ?? '');
                  }}
                  data={current.map((r) => ({
                    value: String(r.id),
                    label: `${r.person} · ${recordedTime(r.started_at)}`
                  }))}
                />
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <TextInput
                    label={t`Corrected start (ISO time with zone)`}
                    value={start}
                    onChange={(e) => setStart(e.currentTarget.value)}
                  />
                  <TextInput
                    label={t`Corrected end (ISO time with zone)`}
                    value={end}
                    onChange={(e) => setEnd(e.currentTarget.value)}
                  />
                </SimpleGrid>
                <Textarea
                  label={t`Correction reason`}
                  value={reason}
                  onChange={(e) => setReason(e.currentTarget.value)}
                />
                <Button
                  disabled={
                    locked || !selected || !reason.trim() || !start || !end
                  }
                  onClick={() =>
                    command.submit(
                      'correct',
                      {
                        interval_id: Number(selected),
                        started_at: start,
                        ended_at: end,
                        reason
                      },
                      version
                    )
                  }
                >{t`Save correction`}</Button>
                <Text
                  size='xs'
                  c='dimmed'
                >{t`The original interval stays in history.`}</Text>
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
      <RecordCommandState controller={command} />
    </Stack>
  );
}
