import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import type { CriticalDesignation } from '@lib/types/RecordedFacts';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Checkbox,
  Loader,
  Paper,
  Stack,
  Text,
  Textarea
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../App';
import { RecordCommandState } from '../../components/records/RecordCommandState';
import { useRecordedCommand } from '../../components/records/useRecordedCommand';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import { recordedTime } from '../tasks/workorders/execution/ExecutionFacts';

type Binding = {
  active?: boolean;
  external_key: string;
  source_name: string;
  unit: string;
  binding_signature: string;
  binding_id: number;
  display_name: string;
  history: CriticalDesignation[];
};
export function CriticalSignalReview({
  machineId
}: Readonly<{ machineId: number }>) {
  const user = useUserState();
  const host = useLocalState((state) => state.hostKey);
  const endpoint = `${apiUrl(ApiEndpoints.equipment_registry).replace(/registry\/$/, 'recorded/')}${machineId}/critical-signals/`;
  const query = useQuery({
    queryKey: [
      'registry',
      'critical',
      host,
      user.user?.pk,
      user.authGeneration,
      machineId
    ],
    queryFn: async () => (await api.get<{ results: Binding[] }>(endpoint)).data
  });
  return (
    <Stack gap='sm'>
      <Text size='sm'>{t`Engineers designate the signals required for critical coverage. Limits alone do not designate a signal. Unmarked signals stay not designated.`}</Text>
      {query.isPending ? (
        <Loader />
      ) : query.isError ? (
        <Alert color='orange'>
          {t`Critical designations could not be read.`}
          <Button onClick={() => query.refetch()}>{t`Retry`}</Button>
        </Alert>
      ) : query.data.results.length ? (
        query.data.results.map((row) => (
          <CriticalBinding
            key={row.binding_id}
            row={row}
            endpoint={endpoint}
            refresh={() => query.refetch()}
          />
        ))
      ) : (
        <Text>{t`No active bindings returned.`}</Text>
      )}
    </Stack>
  );
}
function CriticalBinding({
  row,
  endpoint,
  refresh
}: Readonly<{ row: Binding; endpoint: string; refresh: () => unknown }>) {
  const user = useUserState();
  const latest = row.history[0];
  const [critical, setCritical] = useState(
    Boolean(latest?.critical && latest.applies_current !== false)
  );
  const [reason, setReason] = useState('');
  const [baseVersion, setBaseVersion] = useState(latest?.version ?? 0);
  const [baseSignature, setBaseSignature] = useState(row.binding_signature);
  const changed =
    (latest?.version ?? 0) > baseVersion ||
    baseSignature !== row.binding_signature;
  const command = useRecordedCommand<CriticalDesignation>(
    endpoint,
    (saved) => {
      setBaseVersion(saved.version);
      setCritical(saved.critical);
      setReason('');
      refresh();
    },
    [
      ['registry', 'critical'],
      ['machine-health-signals'],
      ['machine-health-summary']
    ],
    String(row.binding_id)
  );
  return (
    <Paper
      withBorder
      p='md'
      radius='md'
      data-testid={`critical-binding-${row.binding_id}`}
    >
      <Stack gap='xs'>
        <Text fw={600}>{row.display_name}</Text>
        <Text size='xs' c='dimmed' style={{ overflowWrap: 'anywhere' }}>
          {row.source_name} · {row.external_key} ·{' '}
          {row.unit || t`Unit not recorded`}
        </Text>
        {changed && (
          <Alert color='yellow'>
            {t`Designation or binding changed while this form was open. Review the latest binding before recording.`}
            <Button
              variant='default'
              onClick={() => {
                setBaseVersion(latest?.version ?? 0);
                setBaseSignature(row.binding_signature);
                setCritical(
                  Boolean(latest?.critical && latest.applies_current !== false)
                );
              }}
            >{t`Use latest designation`}</Button>
          </Alert>
        )}
        {row.active === false && (
          <Text size='xs' c='dimmed'>
            {latest?.critical && latest.applies_current !== false
              ? t`Inactive binding. A retained critical designation remains missing coverage until an engineer revises it.`
              : t`Inactive binding.`}
          </Text>
        )}
        <Text size='sm'>
          {latest?.critical && latest.applies_current === false
            ? t`Designation review required — binding meaning changed`
            : latest?.critical && latest.applies_current !== false
              ? t`Designated critical`
              : t`Not designated`}
        </Text>
        {latest && (
          <Text size='xs' c='dimmed'>
            {latest.reason} · {latest.recorded_by ?? t`Not recorded`} ·{' '}
            {recordedTime(latest.recorded_at)} · {t`Version ${latest.version}`}
          </Text>
        )}
        {user.hasChangeRole(UserRoles.admin) && (
          <>
            <Checkbox
              label={t`Critical signal`}
              checked={critical}
              onChange={(e) => setCritical(e.currentTarget.checked)}
            />
            <Textarea
              label={t`Designation reason`}
              value={reason}
              onChange={(e) => setReason(e.currentTarget.value)}
            />
            <Button
              disabled={
                !reason.trim() ||
                !!command.retained ||
                changed ||
                !baseSignature
              }
              loading={command.pending}
              onClick={() =>
                command.submit(
                  'designate',
                  {
                    binding_id: row.binding_id,
                    binding_signature: baseSignature,
                    critical,
                    reason
                  },
                  baseVersion
                )
              }
            >{t`Record designation`}</Button>
          </>
        )}
        {row.history.length > 1 && (
          <details>
            <summary>{t`Designation history`}</summary>
            {row.history.slice(1).map((d) => (
              <Text key={d.version} size='sm'>
                {t`Version ${d.version}`} ·{' '}
                {d.critical ? t`Designated critical` : t`Not designated`} ·{' '}
                {d.reason} · {d.recorded_by} · {recordedTime(d.recorded_at)}
              </Text>
            ))}
          </details>
        )}
        <RecordCommandState controller={command} />
      </Stack>
    </Paper>
  );
}
