import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { Alert, Button, Stack, Text } from '@mantine/core';
import { ExecutionFact, recordedTime } from './ExecutionFacts';
import { useContinuityCommand } from './useContinuityCommand';

/** Incoming acceptance is a command, independent of assignment and reading. */
export function HandoverAcceptance({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const controller = useContinuityCommand(workOrder.id, ':handover');
  const value = workOrder.work_continuity?.accepted_handover;
  const state = controller.state;
  if (!value?.available)
    return (
      <>
        <ExecutionFact
          label={t`Accepted incoming owner / accepted at`}
        >{t`Not recorded`}</ExecutionFact>
        <ExecutionFact
          label={t`Site acceptance requirement / escalation owner`}
        >{t`Not supplied`}</ExecutionFact>
        <Text size='sm'>{t`Assignment, a read receipt and opening this brief or chat do not accept responsibility. No accepted-handover record or site acceptance action is supplied here. Continue through the existing site process.`}</Text>
      </>
    );
  const busy = ['pending', 'uncertain'].includes(state.status);
  return (
    <Stack gap='xs' data-testid='handover-acceptance'>
      <Alert
        color={value.state === 'pending' ? 'orange' : 'blue'}
        title={
          value.state === 'pending'
            ? t`Handover awaiting acceptance`
            : value.state === 'accepted'
              ? t`Handover accepted`
              : value.state === 'released'
                ? t`Assignment released`
                : t`Handover proposal withdrawn`
        }
      >
        <Text size='sm'>
          {t`Responsible owner`}: {value.responsible_owner || t`Not recorded`}
        </Text>
        <Text size='sm'>
          {t`Incoming owner`}: {value.incoming_owner || t`Not recorded`}
        </Text>
        {value.state === 'pending' ? (
          <Text size='sm'>{t`The previous owner stays responsible until the incoming owner selects Accept handover. Assignment and a read receipt are not acceptance.`}</Text>
        ) : value.state === 'accepted' ? (
          <Text size='sm'>
            {value.accepted_by} · {recordedTime(value.accepted_at)} ·{' '}
            {t`Work version`}: {value.accepted_version}
          </Text>
        ) : (
          <Text size='sm'>{t`No incoming acceptance is pending. The recorded responsible owner is retained; releasing or restoring assignment does not record a new acceptance.`}</Text>
        )}
      </Alert>
      <Text size='sm'>
        {t`Proposed by`}: {value.proposed_by || t`Not recorded`} ·{' '}
        {recordedTime(value.proposed_at)} · v{value.proposed_version}
      </Text>
      {state.status === 'confirmed' &&
        state.intent.handoverId === value.id &&
        value.state === 'accepted' && (
          <Text size='sm'>{t`Acceptance confirmed in the saved Work record.`}</Text>
        )}
      {state.status === 'uncertain' && (
        <Alert color='orange'>{t`Acceptance outcome unknown. Check the original operation before preparing another acceptance.`}</Alert>
      )}
      {(state.status === 'failed' || state.status === 'stale') && (
        <Alert color='orange'>
          <Text size='sm'>{state.message}</Text>
          <Button
            variant='light'
            onClick={() => void controller.reviewRefusal()}
          >{t`Review latest Work`}</Button>
        </Alert>
      )}
      {state.status === 'blocked' && (
        <Alert color='orange'>{t`The retained acceptance cannot be read. Review the saved Work history.`}</Alert>
      )}
      {value.can_accept &&
        typeof value.id === 'number' &&
        ['idle', 'confirmed'].includes(state.status) && (
          <Button
            mih={44}
            disabled={!controller.available || busy}
            onClick={() =>
              void controller.execute({
                workOrderId: workOrder.id,
                action: 'accept_handover',
                handoverId: value.id ?? undefined,
                fromStatus: workOrder.lifecycle_status,
                payload: {
                  expected_version: workOrder.lifecycle_version,
                  idempotency_key: crypto.randomUUID(),
                  reason: ''
                }
              })
            }
          >{t`Accept handover`}</Button>
        )}
      {state.status === 'uncertain' && (
        <Button
          mih={44}
          variant='light'
          onClick={() => void controller.execute()}
        >{t`Check original acceptance`}</Button>
      )}
    </Stack>
  );
}
