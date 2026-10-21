import type { RecurrencePolicy } from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { Alert, Anchor, Button, Paper, Stack, Text } from '@mantine/core';
import { Link } from 'react-router-dom';
import { recordedTime } from './ExecutionFacts';
import { useProcessCommand } from './useProcessCommand';

const buttonStyles = {
  root: { height: 'auto', minHeight: 44, paddingBlock: 8, maxWidth: '100%' },
  label: {
    whiteSpace: 'normal' as const,
    overflow: 'visible',
    textAlign: 'center' as const
  }
};
const alertStyles = { body: { minWidth: 0 } };

export function RecurrenceActions({
  workOrder: work
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const policy = work.recurrence_policy;
  if (policy?.rule !== 'recurrence_policy.v1')
    return (
      <Text size='sm'>{t`Recurrence linkage and reopen policy are unavailable here. Prior accepted fields and original evidence remain inspectable; a complete immutable accepted report is not supplied by this server.`}</Text>
    );
  return (
    <Stack gap='xs' data-testid='recurrence-policy'>
      {policy.linked_from && (
        <Anchor
          component={Link}
          to={`/maintenance/work-orders/${policy.linked_from.id}/`}
          size='sm'
        >
          {t`Previous closed Work`}:{' '}
          {policy.linked_from.reference || policy.linked_from.id}
        </Anchor>
      )}
      <Text
        fw={600}
        size='sm'
      >{t`Same-component recurrence policy · ${policy.days} days`}</Text>
      <Text size='sm'>{t`A recorded same-component deviation within the closure window can create linked corrective work. Reopening requires an enabled closure-verification policy, a closure awaiting verification and retained evidence that permits reopening. Opening this panel makes no changes; prior closure records stay unchanged.`}</Text>
      {!policy.available ? (
        <Text size='sm'>{t`Recurrence eligibility is unavailable for this Work’s authorized component and closure facts.`}</Text>
      ) : policy.items.length === 0 ? (
        <Text size='sm'>{t`No eligible deviations returned in this bounded policy view. This is not proof that the component is healthy.`}</Text>
      ) : (
        policy.items.map((row) => (
          <RecurrenceRow
            key={row.occurrence_id}
            work={work}
            row={row}
            canCreate={!!policy.can_create}
            canReopen={!!policy.can_reopen}
          />
        ))
      )}
    </Stack>
  );
}
function RecurrenceRow({
  work,
  row,
  canCreate,
  canReopen
}: Readonly<{
  work: WorkOrderOverview;
  row: RecurrencePolicy['items'][number];
  canCreate: boolean;
  canReopen: boolean;
}>) {
  const controller = useProcessCommand(
    'recurrence',
    work.id,
    row.occurrence_id
  );
  const { state } = controller;
  return (
    <Paper withBorder p='sm' style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
      <Stack gap='xs'>
        <Text fw={600} size='sm'>
          {row.title} · {row.component_ref}
        </Text>
        <Anchor
          component={Link}
          to={`/machines/machine/${work.machine}/fault-history/?occurrence=${row.occurrence_id}`}
          size='sm'
        >
          {t`Retained deviation`} · {recordedTime(row.observed_at)}
        </Anchor>
        {row.work_order_id && (
          <Anchor
            component={Link}
            to={`/maintenance/work-orders/${row.work_order_id}/`}
            size='sm'
          >
            {t`Linked corrective Work`}: {row.work_order_id}
          </Anchor>
        )}
        {row.closure_acceptance === 'unknown' && (
          <Text size='sm'>
            {row.action === 'linked' && row.work_order_id
              ? t`Closure acceptance is not recorded. Linked corrective Work is already recorded for this deviation.`
              : row.action === 'create_linked'
                ? canCreate
                  ? t`Closure acceptance is not recorded. This does not permit reopening; create linked corrective work.`
                  : t`Closure acceptance is not recorded. A Work creation role is required for linked corrective work.`
                : t`Closure acceptance is not recorded. This does not permit reopening.`}
          </Text>
        )}
        {row.reopen_blocked_reason && (
          <Text size='sm'>{t`Retained closure evidence prevents reopening. Linked corrective work preserves that history.`}</Text>
        )}
        {row.action === 'unowned_during_rework' && (
          <Alert
            color='orange'
            styles={alertStyles}
          >{t`This deviation has no corrective owner. Rework is already open; its applicability and response must be reviewed through the owning Work process.`}</Alert>
        )}
        {row.action === 'reopen_unavailable' && (
          <Alert
            color='gray'
            styles={alertStyles}
          >{t`Reopening is unavailable for this closure’s retained evidence. Its owning completion flow must preserve that history before work can reopen. The original account is unchanged.`}</Alert>
        )}
        {row.action === 'already_reopened' && (
          <Text size='sm'>{t`Work already reopened; the prior closure remains inspectable.`}</Text>
        )}
        {row.action === 'reopen' && !canReopen && (
          <Text size='sm'>{t`Reopening requires native Work change and execution permission.`}</Text>
        )}
        {canCreate &&
          (row.action !== 'reopen' || canReopen) &&
          ['create_linked', 'reopen'].includes(row.action) &&
          state.status === 'idle' && (
            <Button
              styles={buttonStyles}
              mih={44}
              disabled={!controller.available}
              onClick={() =>
                void controller.execute({
                  kind: 'recurrence',
                  workId: work.id,
                  targetId: row.occurrence_id,
                  url: '/api/kanban/work-orders/commands/create/',
                  payload: {
                    machine: work.machine,
                    title: Array.from(row.title).slice(0, 200).join(''),
                    recurrence_of: work.id,
                    recurrence_occurrence: row.occurrence_id,
                    prior_version: work.lifecycle_version,
                    idempotency_key: crypto.randomUUID()
                  }
                })
              }
            >
              {row.action === 'reopen'
                ? t`Reopen unaccepted Work`
                : t`Create linked corrective work`}
            </Button>
          )}
        {state.status === 'pending' && (
          <Text size='sm'>{t`Recording recurrence work…`}</Text>
        )}
        {state.status === 'confirmed' && (
          <Text size='sm'>
            {state.result?.outcome === 'reopened'
              ? t`Work reopened; the previous unaccepted closure is retained.`
              : state.result?.outcome === 'linked_created'
                ? t`Linked corrective Work ${state.result.reference || state.result.workId} created; the previous closure is unchanged.`
                : t`Original recurrence receipt retained. Review the saved Work to inspect its outcome.`}
          </Text>
        )}
        {state.status === 'refused' && (
          <Alert color='orange' styles={alertStyles}>
            {state.message ||
              t`Recurrence command refused. Review the latest saved Work.`}
          </Alert>
        )}
        {(state.status === 'refused' || state.status === 'confirmed') && (
          <Button
            styles={buttonStyles}
            variant='light'
            onClick={() => void controller.review()}
          >{t`Review latest recurrence`}</Button>
        )}
        {state.status === 'uncertain' && (
          <Alert color='orange' styles={alertStyles}>
            <Stack gap='xs'>
              <Text size='sm'>{t`Recurrence outcome unknown. Reconcile the original command before creating anything else.`}</Text>
              <Button
                styles={buttonStyles}
                variant='light'
                onClick={() => void controller.execute()}
              >{t`Check original recurrence command`}</Button>
            </Stack>
          </Alert>
        )}
      </Stack>
    </Paper>
  );
}
