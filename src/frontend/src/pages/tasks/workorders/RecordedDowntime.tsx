import { UserRoles } from '@lib/enums/Roles';
import type { DowntimeProposal, TimeResult } from '@lib/types/RecordedFacts';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../../App';
import {
  InfoTooltip,
  MissingValue
} from '../../../components/items/ValueTooltip';
import { RecordCommandState } from '../../../components/records/RecordCommandState';
import { useRecordedCommand } from '../../../components/records/useRecordedCommand';
import { useUserState } from '../../../states/UserState';
import { recordedTime } from './execution/ExecutionFacts';
import { workTimeEndpoint } from './execution/RecordedWorkTime';

export function RecordedDowntime({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const user = useUserState();
  const [saved, setSaved] = useState<TimeResult | null>(null);
  const records =
    saved && saved.version >= workOrder.lifecycle_version
      ? saved.downtime
      : workOrder.time_assignment?.downtime_records;
  const [proposals, setProposals] = useState<DowntimeProposal[]>([]);
  const [missing, setMissing] = useState('');
  const [selected, setSelected] = useState<string | null>(null);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [reason, setReason] = useState('');
  const [original, setOriginal] = useState<string | null>(null);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const endpoint = workTimeEndpoint(workOrder.id);
  const command = useRecordedCommand<TimeResult>(
    endpoint,
    setSaved,
    [['work-order']],
    'downtime'
  );
  const read = useMutation({
    mutationFn: async () =>
      (
        await api.get<{ proposals: DowntimeProposal[]; reason: string | null }>(
          endpoint,
          {
            params: {
              propose: true,
              ...(from ? { from } : {}),
              ...(to ? { to } : {})
            }
          }
        )
      ).data,
    onMutate: () => {
      setProposals([]);
      setMissing('');
      setSelected(null);
    },
    onSuccess: (result) => {
      setProposals(result.proposals);
      setMissing(result.reason ?? '');
      setSelected(null);
    }
  });
  const choice = proposals.find((_, i) => String(i) === selected);
  return (
    <Stack gap='sm' data-testid='recorded-downtime'>
      <Text
        fw={600}
        size='sm'
      >{t`Machine downtime from run/stop telemetry`}</Text>
      {records?.length ? (
        records.map((row) => (
          <Paper key={row.id} withBorder p='sm'>
            <Text size='sm' fw={600}>
              {row.signal} ·{' '}
              {row.superseded
                ? t`Superseded original`
                : t`Confirmed machine stop window`}
            </Text>
            <Text size='sm'>
              {recordedTime(row.started_at)} → {recordedTime(row.ended_at)}
            </Text>
            <Text size='xs' c='dimmed'>
              {t`Recorded by`}: {row.recorded_by ?? t`Not recorded`} ·{' '}
              {recordedTime(row.recorded_at)}
            </Text>
            {row.reason && <Text size='sm'>{row.reason}</Text>}
          </Paper>
        ))
      ) : (
        <MissingValue
          reason={t`Machine downtime: not recorded. No confirmed run/stop window is supplied.`}
        />
      )}
      <InfoTooltip
        label={t`Service downtime: unavailable — no sourced outage interval or service-impact measure is supplied. Optional account minutes are technician-entered account data, not a verified service outage.`}
      />
      {user.hasChangeRole(UserRoles.work_order) && (
        <details>
          <summary>{t`Read telemetry and confirm downtime`}</summary>
          <Stack gap='sm' mt='sm'>
            <Text
              size='xs'
              c='dimmed'
            >{t`Reads one bounded window from the mapped run/stop signal. Blank dates use the last hour. Gaps, shifted demonstration clocks and open stop windows are not proposed.`}</Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput
                label={t`Telemetry from (ISO time with zone)`}
                value={from}
                onChange={(e) => setFrom(e.currentTarget.value)}
              />
              <TextInput
                label={t`Telemetry to (ISO time with zone)`}
                value={to}
                onChange={(e) => setTo(e.currentTarget.value)}
              />
            </SimpleGrid>
            <Button
              variant='default'
              loading={read.isPending}
              onClick={() => read.mutate()}
            >{t`Find run/stop windows`}</Button>
            {read.isError && (
              <Alert color='orange'>{t`Telemetry could not be read. Downtime remains not recorded.`}</Alert>
            )}
            {missing && <Text size='sm'>{missing}</Text>}
            {!!proposals.length && (
              <>
                <Select
                  label={t`Proposed stop window`}
                  value={selected}
                  onChange={(id) => {
                    setSelected(id);
                    const row = proposals[Number(id)];
                    setStart(row?.started_at ?? '');
                    setEnd(row?.ended_at ?? '');
                  }}
                  data={proposals.map((row, i) => ({
                    value: String(i),
                    label: `${row.signal} · ${recordedTime(row.started_at)} → ${recordedTime(row.ended_at)}`
                  }))}
                />
                <SimpleGrid cols={{ base: 1, sm: 2 }}>
                  <TextInput
                    label={t`Confirmed stop start`}
                    value={start}
                    onChange={(e) => setStart(e.currentTarget.value)}
                  />
                  <TextInput
                    label={t`Confirmed stop end`}
                    value={end}
                    onChange={(e) => setEnd(e.currentTarget.value)}
                  />
                </SimpleGrid>
                {!!records?.filter((r) => !r.superseded).length && (
                  <Select
                    clearable
                    label={t`Correct an existing downtime record`}
                    value={original}
                    onChange={setOriginal}
                    data={records
                      .filter((r) => !r.superseded)
                      .map((r) => ({
                        value: String(r.id),
                        label: `${r.signal} · ${recordedTime(r.started_at)}`
                      }))}
                  />
                )}
                <Textarea
                  label={t`Downtime correction reason`}
                  description={t`Required if you correct the proposed times or a recorded window.`}
                  value={reason}
                  onChange={(e) => setReason(e.currentTarget.value)}
                />
                <Button
                  disabled={!choice || !start || !end || !!command.retained}
                  loading={command.pending}
                  onClick={() =>
                    choice &&
                    command.submit(
                      'downtime',
                      {
                        token: choice.token,
                        started_at: start,
                        ended_at: end,
                        reason,
                        ...(original ? { supersedes: Number(original) } : {})
                      },
                      Math.max(saved?.version ?? 0, workOrder.lifecycle_version)
                    )
                  }
                >{t`Confirm machine downtime`}</Button>
              </>
            )}
          </Stack>
        </details>
      )}
      <RecordCommandState controller={command} />
    </Stack>
  );
}
