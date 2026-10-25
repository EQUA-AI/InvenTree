import type { CloseoutWorkOrder } from '@lib/types/Closeout';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Card,
  Group,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useRetainedCommand } from '../../../hooks/useRetainedCommand';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';

export function CloseoutPreparation({
  workOrder,
  packetId,
  locked,
  onChanged
}: Readonly<{
  workOrder: CloseoutWorkOrder;
  packetId?: number;
  locked: boolean;
  onChanged: () => void;
}>) {
  const api = useApi();
  const user = useUserState((state) => state.user);
  const generation = useUserState((state) => state.authGeneration);
  const host = useLocalState((state) => state.hostKey);
  const steps: Record<
    string,
    { target: string; action: string; label: string }
  > = {
    draft: { target: 'planned', action: 'plan', label: t`Plan this Work` },
    planned: {
      target: 'ready',
      action: 'mark_ready',
      label: t`Check and mark ready`
    },
    ready: {
      target: 'in_progress',
      action: 'start',
      label: t`Start execution`
    },
    on_hold: {
      target: 'in_progress',
      action: 'resume',
      label: t`Resume execution`
    },
    in_progress: {
      target: 'verifying',
      action: 'verify',
      label: t`Finish execution and begin closeout`
    }
  };
  const step = steps[workOrder.lifecycle_status];
  const statusLabel =
    (
      {
        draft: t`Draft`,
        planned: t`Planned`,
        ready: t`Ready`,
        in_progress: t`In progress`,
        on_hold: t`On hold`,
        verifying: t`Verifying`,
        completed: t`Completed`,
        canceled: t`Canceled`
      } as Record<string, string>
    )[workOrder.lifecycle_status] ?? t`Status not supplied`;
  const retained = useRetainedCommand(
    `closeout-transition:${workOrder.id}`,
    new RegExp(`^/api/tasks/work-orders/${workOrder.id}/transition/$`)
  );
  const readiness = useQuery({
    queryKey: [
      'closeout-next-step',
      host,
      user?.pk,
      generation,
      workOrder.id,
      workOrder.lifecycle_version,
      step?.action
    ],
    enabled: !!step && !packetId,
    retry: false,
    queryFn: async () =>
      (
        await api.get<{
          ready: boolean;
          lifecycle_version: number;
          blockers: { code: string; message: string }[];
        }>(
          `/api/tasks/work-orders/${workOrder.id}/readiness/?action=${step!.action}`
        )
      ).data
  });
  if (!step) return null;
  const permission = step.target === 'in_progress' ? 'execute' : 'transition';
  const allowed =
    user?.is_superuser || user?.permissions?.workorder?.includes(permission);
  const blocked =
    locked ||
    !allowed ||
    readiness.isFetching ||
    readiness.isError ||
    !readiness.data?.ready ||
    readiness.data.lifecycle_version !== workOrder.lifecycle_version ||
    retained.storageFailed ||
    retained.busy;
  const advance = async () => {
    if (blocked) return;
    const result = await retained.execute<{
      work_order_id: number;
      command: string;
      event_id: number;
      idempotency_key: string;
      lifecycle_status: string;
    }>(
      {
        url: `/api/tasks/work-orders/${workOrder.id}/transition/`,
        body: {
          expected_version: workOrder.lifecycle_version,
          idempotency_key: crypto.randomUUID(),
          to_status: step.target
        }
      },
      (receipt, request) =>
        receipt.work_order_id === workOrder.id &&
        receipt.command === 'transition' &&
        receipt.event_id > 0 &&
        receipt.idempotency_key === request.body.idempotency_key &&
        receipt.lifecycle_status === request.body.to_status
    );
    if (result) onChanged();
  };
  return (
    <Card withBorder data-testid='closeout-preparation'>
      <Stack gap='sm'>
        <Title order={3} size='h5'>{t`Before you close out`}</Title>
        <Text>
          {t`Current Work state`}: {statusLabel}.{' '}
          {t`Complete the required preparation and execution steps before submitting the final service report.`}
        </Text>
        {packetId ? (
          <Alert color='orange'>
            {t`The linked Repair Packet owns these transitions. Continue through its native safety and execution workflow.`}
            <Anchor
              component={Link}
              to={`/repair/packets/${packetId}/`}
              display='block'
              mt='xs'
            >{t`Open Repair Packet`}</Anchor>
          </Alert>
        ) : (
          <>
            {!allowed && (
              <Alert color='orange'>{t`You do not have permission for the next Work transition. Ask the authorized planner or technician.`}</Alert>
            )}
            {readiness.isError && (
              <Alert color='red'>{t`Readiness could not be checked. No transition is available until it is reloaded.`}</Alert>
            )}
            {readiness.data &&
              readiness.data.lifecycle_version !==
                workOrder.lifecycle_version && (
                <Alert color='orange'>
                  {t`This Work changed in another session. Reload the Work and readiness before continuing.`}
                  <Button
                    variant='subtle'
                    onClick={() => {
                      onChanged();
                      void readiness.refetch();
                    }}
                  >{t`Reload readiness`}</Button>
                </Alert>
              )}
            {readiness.data?.blockers.map((blocker) => (
              <Alert color='orange' key={blocker.code}>
                {blocker.message}
              </Alert>
            ))}
            {retained.error && <Alert color='red'>{retained.error}</Alert>}
            <Group>
              <Button disabled={blocked} onClick={() => void advance()}>
                {retained.intent ? t`Retry same transition` : step.label}
              </Button>
              <Button
                variant='subtle'
                disabled={retained.busy}
                onClick={() => void readiness.refetch()}
              >{t`Reload readiness`}</Button>
            </Group>
          </>
        )}
        <Group>
          <Anchor
            component={Link}
            to='?work_panel=plan'
          >{t`Review assignment and plan`}</Anchor>
          <Anchor
            component={Link}
            to='?work_panel=work'
          >{t`Review execution and required steps`}</Anchor>
        </Group>
      </Stack>
    </Card>
  );
}
