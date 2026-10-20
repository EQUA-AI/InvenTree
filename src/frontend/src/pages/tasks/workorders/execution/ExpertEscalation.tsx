import { UserRoles } from '@lib/enums/Roles';
import type { InvestigationRequest } from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Select,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useApi } from '../../../../contexts/ApiContext';
import { useLocalState } from '../../../../states/LocalState';
import { useUserState } from '../../../../states/UserState';
import { recordedTime } from './ExecutionFacts';
import { useProcessCommand } from './useProcessCommand';

/** A persistent contradiction can be routed to one real authorized group member. */
export function ExpertEscalation({
  workOrder,
  request
}: Readonly<{ workOrder: WorkOrderOverview; request: InvestigationRequest }>) {
  const canChange = useUserState((s) => s.hasChangeRole(UserRoles.work_order));
  const actor = useUserState((s) => s.user?.pk);
  const generation = useUserState((s) => s.authGeneration);
  return (
    <Stack gap='xs' data-testid={`expert-escalation-${request.id}`}>
      {request.escalation && (
        <Alert
          color={request.escalation.state === 'active' ? 'orange' : 'gray'}
          title={t`Expert escalation`}
        >
          <Text size='sm'>
            {request.escalation.group} ·{' '}
            {request.escalation.state === 'active'
              ? t`Active — unresolved`
              : t`Resolved`}
          </Text>
          <Text size='sm'>
            {t`Owner`}: {request.owner_name || t`Not recorded`} ·{' '}
            {t`Checkpoint`}: {recordedTime(request.next_checkpoint)}
          </Text>
          <Text size='sm'>
            {t`Recorded by`}:{' '}
            {request.escalation.recorded_by || t`Not recorded`} ·{' '}
            {recordedTime(request.escalation.recorded_at)}
          </Text>
          <Text size='sm'>
            {t`Routing reason`}: {request.escalation.reason || t`Not recorded`}
          </Text>
          <Text size='sm'>{t`Routing is recorded. Delivery and expert acceptance are not established by this record.`}</Text>
        </Alert>
      )}
      {canChange && request.status !== 'resolved' && (
        <EscalationForm
          key={`${actor}:${generation}:${workOrder.id}:${request.id}`}
          workOrder={workOrder}
          request={request}
        />
      )}
    </Stack>
  );
}
function EscalationForm({
  workOrder: work,
  request
}: Readonly<{ workOrder: WorkOrderOverview; request: InvestigationRequest }>) {
  const api = useApi();
  const actor = useUserState((s) => s.user?.pk);
  const generation = useUserState((s) => s.authGeneration);
  const host = useLocalState((s) => s.getHost());
  const [opened, setOpened] = useState(false);
  const [owner, setOwner] = useState<string | null>(null);
  const [checkpoint, setCheckpoint] = useState('');
  const [reason, setReason] = useState(request.blocked_reason);
  const controller = useProcessCommand('escalation', work.id, request.id);
  const { state } = controller;
  const options = useQuery({
    queryKey: ['escalation-owners', host, actor, generation, work.id],
    enabled: opened,
    retry: false,
    queryFn: async () => {
      const response = await api.get(
        `/api/kanban/work-orders/${work.id}/investigation-requests/?escalation_owners=1`
      );
      if (
        typeof response.data?.group !== 'string' ||
        typeof response.data?.available !== 'boolean' ||
        !Array.isArray(response.data?.owners)
      )
        throw new Error('Expert owners unavailable');
      return response.data as {
        group: string;
        available?: boolean;
        reason?: string;
        owners: { id: number; name: string }[];
      };
    }
  });
  return (
    <Stack gap='xs'>
      {state.status !== 'confirmed' && (
        <Button variant='light' mih={44} onClick={() => setOpened(!opened)}>
          {opened ? t`Hide expert routing` : t`Escalate to expert group`}
        </Button>
      )}
      {opened && state.status !== 'confirmed' && (
        <Stack gap='xs'>
          <Text fw={600} size='sm'>
            {options.data?.group || t`Expert group not supplied`}
          </Text>
          {options.isPending ? (
            <Text size='sm'>{t`Reading eligible expert owners…`}</Text>
          ) : options.isError ? (
            <Alert color='gray'>{t`Expert owners unavailable. No owner has been selected.`}</Alert>
          ) : options.data?.available === false ? (
            <Alert color='orange'>{t`Expert group not configured. The configured group must be created through native administration before an owner can be selected.`}</Alert>
          ) : (
            <>
              {options.data?.owners.length === 0 && (
                <Alert color='gray'>{t`No authorized active members are available in this group. Add membership and Work access through the native administration process.`}</Alert>
              )}
              <Select
                label={t`Expert owner`}
                data={(options.data?.owners ?? []).map((o) => ({
                  value: String(o.id),
                  label: o.name
                }))}
                value={owner}
                onChange={setOwner}
                disabled={state.status !== 'idle'}
              />
              <TextInput
                type='datetime-local'
                label={t`Next expert checkpoint (browser local time)`}
                value={checkpoint}
                onChange={(e) => setCheckpoint(e.currentTarget.value)}
                disabled={state.status !== 'idle'}
              />
              <Textarea
                label={t`Escalation reason / persistent contradiction`}
                value={reason}
                onChange={(e) => setReason(e.currentTarget.value)}
                disabled={state.status !== 'idle'}
                autosize
                minRows={2}
              />
              {state.status === 'idle' && (
                <Button
                  mih={44}
                  disabled={
                    !controller.available ||
                    !owner ||
                    !checkpoint ||
                    !reason.trim() ||
                    !Number.isFinite(Date.parse(checkpoint)) ||
                    Date.parse(checkpoint) <= Date.now()
                  }
                  onClick={() =>
                    void controller.execute({
                      kind: 'escalation',
                      workId: work.id,
                      targetId: request.id,
                      url: `/api/kanban/work-orders/${work.id}/investigation-requests/${request.id}/`,
                      payload: {
                        operation: 'escalate',
                        expected_version: work.lifecycle_version,
                        request_version: request.version,
                        idempotency_key: crypto.randomUUID(),
                        fields: {
                          owner_id: Number(owner),
                          next_checkpoint: new Date(checkpoint).toISOString(),
                          blocked_reason: reason.trim()
                        }
                      }
                    })
                  }
                >{t`Record expert escalation`}</Button>
              )}
            </>
          )}
        </Stack>
      )}
      {state.status === 'pending' && (
        <Text size='sm'>{t`Recording expert escalation…`}</Text>
      )}
      {state.status === 'confirmed' &&
        request.escalation &&
        state.confirmedVersion === request.version && (
          <Text size='sm'>{t`Expert escalation confirmed in the saved request.`}</Text>
        )}
      {state.status === 'refused' && (
        <Alert color='orange'>
          {state.message ||
            t`Escalation refused. Review the latest saved request before preparing another routing decision.`}
        </Alert>
      )}
      {state.status === 'uncertain' && (
        <Alert color='orange'>
          <Stack gap='xs'>
            <Text size='sm'>{t`Escalation outcome unknown. The original owner, checkpoint and operation are retained.`}</Text>
            <Button
              variant='light'
              onClick={() => void controller.execute()}
            >{t`Check original escalation`}</Button>
          </Stack>
        </Alert>
      )}
      {(state.status === 'refused' || state.status === 'confirmed') && (
        <Button
          variant='light'
          onClick={() => void controller.review()}
        >{t`Review latest routing`}</Button>
      )}
    </Stack>
  );
}
