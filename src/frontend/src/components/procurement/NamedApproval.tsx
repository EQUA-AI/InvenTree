import { t } from '@lingui/core/macro';
import { Alert, Button, Select, Stack, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from '../../App';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import { ApprovalInboxPanel } from '../ai/ApprovalInboxPanel';
import { CommandNotice } from './CommandNotice';
import { approvedLine } from './procurement';
import { useProcurementCommand } from './useProcurementCommand';

export function NamedApproval({
  scope,
  payload,
  summary,
  linkEndpoint,
  onLinked
}: Readonly<{
  scope: string;
  payload: Record<string, unknown>;
  summary: string;
  linkEndpoint?: string;
  onLinked?: () => void;
}>) {
  const actor = useUserState((state) => state.user?.pk);
  const canRequest = useUserState(
    (state) =>
      state.isSuperuser() ||
      !!state.user?.permissions?.approval?.includes('review')
  );
  const host = useLocalState((state) => state.getHost());
  const storage = `aimms:r8:approval:${host}:${actor}:${scope}`;
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [assignee, setAssignee] = useState<string | null>(null);
  const [storageError, setStorageError] = useState(false);
  const query = useQueryClient();
  useEffect(() => {
    try {
      setApprovalId(localStorage.getItem(storage));
    } catch {
      setStorageError(true);
    }
  }, [storage]);
  const command = useProcurementCommand(`approval:${scope}`, (result) => {
    const id = String(result.id);
    try {
      localStorage.setItem(storage, id);
    } catch (error) {
      setStorageError(true);
      throw error;
    }
    setApprovalId(id);
  });
  const link = useProcurementCommand(`approval-link:${scope}`, () => {
    onLinked?.();
  });
  const users = useQuery({
    queryKey: ['procurement-users'],
    retry: false,
    queryFn: async ({ signal }) => {
      const data = (
        await api.get('/api/user/', {
          params: { is_active: true, limit: 500 },
          signal
        })
      ).data;
      return (Array.isArray(data) ? data : data.results) as {
        pk: number;
        username: string;
        first_name: string;
        last_name: string;
      }[];
    }
  });
  const approval = useQuery({
    queryKey: ['procurement-approval', approvalId],
    enabled: !!approvalId,
    retry: false,
    queryFn: async ({ signal }) =>
      (await api.get(`/api/approvals/${approvalId}/`, { signal })).data,
    refetchInterval: approvalId ? 10000 : false
  });
  useEffect(() => {
    const refresh = () => {
      void query.invalidateQueries({
        queryKey: ['procurement-approval', approvalId]
      });
    };
    window.addEventListener('aimms:proposals-refresh', refresh);
    return () => window.removeEventListener('aimms:proposals-refresh', refresh);
  }, [query, approvalId]);
  const line =
    approval.data?.status === 'succeeded'
      ? approvedLine(approval.data.execution_result)
      : null;
  return (
    <Stack gap='sm'>
      <CommandNotice command={command} />
      {storageError && (
        <Alert color='red'>{t`Approval recovery storage unavailable`}</Alert>
      )}
      {approvalId ? (
        <>
          {approval.isError && (
            <Alert color='red'>{t`Approval could not be read`}</Alert>
          )}
          {approval.data && approval.data.assigned_to_user !== actor ? (
            <Alert color='blue'>{t`Awaiting assigned approver`}</Alert>
          ) : (
            <ApprovalInboxPanel
              approvalId={approvalId}
              statuses={[
                'pending',
                'in_review',
                'changes_requested',
                'approved',
                'executing',
                'succeeded',
                'failed'
              ]}
              emptyText={t`Approval unavailable`}
            />
          )}
          {linkEndpoint && line && (
            <Button
              disabled={!!link.retained}
              loading={link.pending}
              onClick={() =>
                void link.submit(linkEndpoint, 'link', {
                  purchase_order_line_id: line
                })
              }
            >{t`Link approved order`}</Button>
          )}
          <CommandNotice command={link} />
        </>
      ) : (
        <>
          <Text size='sm'>{summary}</Text>
          <Select
            label={t`Named approver`}
            required
            searchable
            value={assignee}
            onChange={setAssignee}
            data={(users.data ?? []).map((person) => ({
              value: String(person.pk),
              label:
                [person.first_name, person.last_name]
                  .filter(Boolean)
                  .join(' ') || person.username
            }))}
            error={users.isError ? t`People could not be read` : undefined}
          />
          <Button
            disabled={
              !assignee || !!command.retained || storageError || !canRequest
            }
            loading={command.pending}
            onClick={() =>
              void command.submit('/api/approvals/', 'approval', {
                action_type: 'purchase_order',
                assigned_to_user_id: Number(assignee),
                summary,
                payload
              })
            }
          >{t`Request named approval`}</Button>
        </>
      )}
    </Stack>
  );
}
