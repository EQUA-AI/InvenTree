import type { InvestigationRequest } from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  List,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  Textarea
} from '@mantine/core';
import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { workPanelParams } from '../workPanelState';
import { ContinueInvestigation } from './ContinueInvestigation';
import {
  ExecutionFact,
  ExecutionSection,
  executionBadgeStyles,
  recordedTime
} from './ExecutionFacts';
import { HandoverAcceptance } from './HandoverAcceptance';
import { investigationStatusLabel } from './InvestigationRequests';
import { useContinuityCommand } from './useContinuityCommand';

function lifecycleLabel(state: string) {
  return (
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
    )[state] ?? t`Status not supplied`
  );
}

function HoldResumeRecord({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const controller = useContinuityCommand(workOrder.id);
  const [reason, setReason] = useState('');
  const { state } = controller;
  const confirmedEventId =
    state.status === 'confirmed' ? state.receipt.event_id : null;
  useEffect(() => {
    if (confirmedEventId !== null) setReason('');
  }, [confirmedEventId]);
  const value = workOrder.work_continuity;
  const supplied =
    value?.rule === 'work_continuity.v1' &&
    value.lifecycle_version === workOrder.lifecycle_version;
  const action =
    supplied && value.commands.hold
      ? 'hold'
      : supplied && value.commands.resume
        ? 'resume'
        : null;
  const retained = 'intent' in state && state.status !== 'confirmed';
  const locked = !['idle', 'confirmed'].includes(state.status);
  const waitingForRead =
    state.status === 'confirmed' &&
    workOrder.lifecycle_version < state.receipt.lifecycle_version;
  const receiptEvent =
    state.status === 'confirmed'
      ? value?.history.find(
          (event) => event.event_id === state.receipt.event_id
        )
      : null;
  return (
    <Stack gap='sm' data-testid='hold-resume-record'>
      <Text size='sm' fw={600}>{t`Hold / resume work record`}</Text>
      <Text size='sm'>{t`These actions update this Work record through its existing permissions and server checks. Physical conditions, operating permission and site ownership acceptance remain separate.`}</Text>
      {state.status === 'confirmed' && (
        <Alert
          color='blue'
          title={
            state.intent.action === 'hold'
              ? t`Hold recorded`
              : t`Resume recorded`
          }
        >
          <Text size='sm'>
            {t`Saved work version ${state.receipt.lifecycle_version}`} ·{' '}
            {receiptEvent?.recorded_by ?? t`Recorder not supplied here`} ·{' '}
            {recordedTime(receiptEvent?.recorded_at)}
          </Text>
        </Alert>
      )}
      {(state.status === 'uncertain' || state.status === 'pending') && (
        <Alert
          color='orange'
          title={
            state.status === 'pending'
              ? t`Saving work record`
              : t`Original operation unconfirmed`
          }
        >
          <Text size='sm'>
            {'message' in state && state.message
              ? state.message
              : t`Keep this original operation until its saved result is confirmed. Another hold or resume cannot replace it.`}
          </Text>
          <Text size='sm'>
            {t`Retained reason`}:{' '}
            {state.intent.payload.reason || t`Not recorded`}
          </Text>
        </Alert>
      )}
      {(state.status === 'failed' || state.status === 'stale') && (
        <Alert
          color='orange'
          title={
            state.status === 'stale' ? t`Work changed` : t`Operation refused`
          }
        >
          <Text size='sm'>{state.message}</Text>
          <Text size='sm'>
            {t`Retained reason`}:{' '}
            {state.intent.payload.reason || t`Not recorded`}
          </Text>
        </Alert>
      )}
      {state.status === 'blocked' && (
        <Alert color='orange'>{t`The retained operation cannot be read or saved in this browser. Review the saved Work history; a new command is unavailable here.`}</Alert>
      )}
      {!controller.available && (
        <Text size='sm'>{t`Hold/resume is unavailable in this browser session.`}</Text>
      )}
      {action && !locked && (
        <>
          <Textarea
            label={
              action === 'hold'
                ? t`Reason for hold`
                : t`Record of resume checks (optional)`
            }
            value={reason}
            onChange={(event) => setReason(event.currentTarget.value)}
            autosize
            minRows={2}
            disabled={waitingForRead}
          />
          <Button
            variant={action === 'hold' ? 'light' : 'filled'}
            color={action === 'hold' ? 'orange' : 'blue'}
            style={{ alignSelf: 'flex-start' }}
            mih={44}
            disabled={
              !controller.available ||
              waitingForRead ||
              (action === 'hold' && !reason.trim())
            }
            onClick={() => {
              void controller.execute({
                workOrderId: workOrder.id,
                action,
                payload: {
                  expected_version: workOrder.lifecycle_version,
                  idempotency_key: crypto.randomUUID(),
                  reason: reason.trim()
                }
              });
            }}
          >
            {action === 'hold' ? t`Hold work record` : t`Resume work record`}
          </Button>
        </>
      )}
      {state.status === 'uncertain' && (
        <Button
          variant='light'
          mih={44}
          style={{ alignSelf: 'flex-start' }}
          onClick={() => {
            void controller.execute();
          }}
        >{t`Check original operation`}</Button>
      )}
      {(state.status === 'failed' || state.status === 'stale') && (
        <Button
          variant='light'
          mih={44}
          style={{ alignSelf: 'flex-start' }}
          onClick={() => {
            setReason(state.intent.payload.reason);
            void controller.reviewRefusal();
          }}
        >{t`Review latest Work before retrying`}</Button>
      )}
      {retained && (
        <Text size='sm' c='dimmed'>
          {t`Original work version ${state.intent.payload.expected_version}`} ·{' '}
          {state.intent.action === 'hold' ? t`Hold` : t`Resume`}
        </Text>
      )}
      {!action && !retained && (
        <Text size='sm' c='dimmed'>
          {value?.commands.unavailable_reason === 'packet_owns_lifecycle'
            ? t`The repair packet owns this Work lifecycle. Standalone hold/resume is unavailable for it.`
            : !supplied
              ? t`Hold/resume controls are not supplied by this server.`
              : t`No hold/resume action is available for this Work and your current permission.`}
        </Text>
      )}
    </Stack>
  );
}

function PendingRequest({
  request,
  workOrder
}: Readonly<{ request: InvestigationRequest; workOrder: WorkOrderOverview }>) {
  return (
    <Paper
      withBorder
      p='sm'
      id={`continuity-request-${request.id}`}
      data-testid={`continuity-request-${request.id}`}
    >
      <Stack gap='xs'>
        <Group gap='xs'>
          <Text size='sm' fw={600}>
            {t`Request ${request.id}`} · v{request.version}
          </Text>
          <Badge
            color={
              ['cannot_perform', 'pending_external', 'disputed'].includes(
                request.status
              )
                ? 'orange'
                : 'blue'
            }
            styles={executionBadgeStyles}
          >
            {investigationStatusLabel(request.status)}
          </Badge>
        </Group>
        <Text size='sm' fw={600}>
          {request.next_check}
        </Text>
        <Text size='sm'>
          {t`Decision`}: {request.purpose}
        </Text>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <ExecutionFact label={t`Request owner`}>
            {request.owner_name}
          </ExecutionFact>
          <ExecutionFact label={t`Next checkpoint`}>
            {recordedTime(request.next_checkpoint)}
          </ExecutionFact>
        </SimpleGrid>
        {request.blocked_reason && (
          <Text size='sm'>
            {t`Recorded constraint`}: {request.blocked_reason}
          </Text>
        )}
        {request.required_artifacts.length > 0 && (
          <List size='sm'>
            {request.required_artifacts.map((item, i) => (
              <List.Item key={i}>
                {item.description} · {t`Required conditions`}:{' '}
                {item.conditions || t`Not recorded`}
              </List.Item>
            ))}
          </List>
        )}
        <ContinueInvestigation
          workOrder={workOrder}
          request={request}
          panel='work'
          caption={false}
          variant='light'
          label={t`Discuss request ${request.id}`}
        />
      </Stack>
    </Paper>
  );
}

/** Six-question handover brief assembled from saved Work facts, never an acceptance. */
export function WorkContinuity({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const [params] = useSearchParams();
  const value =
    workOrder.work_continuity?.rule === 'work_continuity.v1'
      ? workOrder.work_continuity
      : null;
  if (!value)
    return (
      <Alert
        color='gray'
        title={t`Work continuity not supplied`}
      >{t`This server does not supply hold provenance or site handover acceptance here. Assignment and opening a panel do not establish accepted responsibility or physical permission.`}</Alert>
    );
  const requests = workOrder.investigation?.requests;
  const pending =
    requests?.available && Array.isArray(requests.items)
      ? requests.items.filter((request) => request.status !== 'resolved')
      : null;
  const verification =
    workOrder.verification_return?.rule === 'verification_return.v1'
      ? workOrder.verification_return
      : null;
  const to = (panel: 'plan' | 'diagnose' | 'parts' | 'verify' | 'history') =>
    `?${workPanelParams(params, panel)}`;
  const hold = workOrder.lifecycle_status === 'on_hold';
  return (
    <ExecutionSection
      title={
        hold ? t`On hold · continuity brief` : t`Work continuity and handover`
      }
      testId='work-continuity'
    >
      <Group gap='xs'>
        <Badge color={hold ? 'orange' : 'gray'} styles={executionBadgeStyles}>
          {lifecycleLabel(workOrder.lifecycle_status)} · v
          {workOrder.lifecycle_version}
        </Badge>
        <Text size='sm'>{t`Saved context for this Work; review before a shift change or resume.`}</Text>
      </Group>
      {hold && (
        <Alert color='orange' title={t`Recorded hold reason`}>
          <Stack gap={4}>
            <Text size='sm'>
              {value?.current_hold?.reason || t`Not recorded`}
            </Text>
            <Text size='sm'>
              {value?.current_hold?.recorded_by || t`Recorder not recorded`} ·{' '}
              {recordedTime(value?.current_hold?.recorded_at)}
            </Text>
            <Text size='sm'>{t`Affected task/request and job-wide resume condition are not linked to this hold. Pending requests below keep their own owners and conditions.`}</Text>
          </Stack>
        </Alert>
      )}
      <Stack gap='sm' data-testid='continuity-pending-requests'>
        <Text fw={600} size='sm'>
          {pending
            ? t`Pending saved requests (${pending.length})`
            : t`Pending request queue not supplied`}
        </Text>
        {pending?.length === 0 && (
          <Text size='sm'>{t`No unresolved requests returned for this Work. This does not establish readiness or close a separate work order.`}</Text>
        )}
        {pending && pending.length > 0 && (
          <SimpleGrid
            cols={{ base: 1, lg: pending.length > 1 ? 2 : 1 }}
            spacing='sm'
          >
            {pending.map((request) => (
              <PendingRequest
                key={request.id}
                request={request}
                workOrder={workOrder}
              />
            ))}
          </SimpleGrid>
        )}
        {pending === null && (
          <Text size='sm'>{t`Owners, constraints and checkpoints cannot be reconstructed from a chat visit or an unavailable queue.`}</Text>
        )}
        <Anchor
          component={Link}
          size='sm'
          to={to('diagnose')}
        >{t`Open investigation and original evidence`}</Anchor>
      </Stack>
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing='md'>
        <Paper withBorder p='sm'>
          <Stack gap='sm'>
            <Text
              fw={600}
              size='sm'
            >{t`Physical state and safe boundary`}</Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <ExecutionFact
                label={t`Equipment physical state`}
              >{t`Not recorded`}</ExecutionFact>
              <ExecutionFact
                label={t`Service / standby availability`}
              >{t`Not recorded`}</ExecutionFact>
              <ExecutionFact
                label={t`Current hazards / prohibited actions`}
              >{t`Not recorded`}</ExecutionFact>
              <ExecutionFact
                label={t`Operating window / access authority`}
              >{t`Not recorded`}</ExecutionFact>
            </SimpleGrid>
            <Text size='sm'>{t`No connected provider supplies these current facts. Prepared gates, a restored lockout point or a Work status do not establish permission to act.`}</Text>
            <Anchor
              component={Link}
              size='sm'
              to={to('plan')}
            >{t`Review recorded boundaries and scope`}</Anchor>
            <Anchor
              component={Link}
              size='sm'
              to={to('verify')}
            >{t`Open saved tests and return records`}</Anchor>
          </Stack>
        </Paper>
        <Paper withBorder p='sm'>
          <Stack gap='sm'>
            <Text
              fw={600}
              size='sm'
            >{t`Responsibility and incoming owner`}</Text>
            <ExecutionFact label={t`Recorded assignee`}>
              {workOrder.time_assignment?.assigned_to ??
                workOrder.assigned_to_name ??
                workOrder.assigned_to_username}
            </ExecutionFact>
            <HandoverAcceptance workOrder={workOrder} />
          </Stack>
        </Paper>
      </SimpleGrid>
      <Accordion variant='separated' multiple keepMounted={false}>
        <Accordion.Item value='performed'>
          <Accordion.Control>{t`Saved work and material context`}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap='sm'>
              <ExecutionFact
                label={t`Saved maintenance account · work performed`}
              >
                {workOrder.structured_closeout?.action ||
                  workOrder.maintenance_record?.summary}
              </ExecutionFact>
              <Text size='sm'>{t`Physical completion and technical acceptance are not inferred from a completed board card.`}</Text>
              <Text
                size='sm'
                fw={600}
              >{t`Recorded material requirements (${workOrder.parts.length})`}</Text>
              {workOrder.parts.length === 0 && (
                <Text size='sm'>{t`No native requirement rows returned. Installed material state is not supplied.`}</Text>
              )}
              {workOrder.parts.map((part) => (
                <Text key={part.id} size='sm'>
                  {part.part_name} · {t`Required quantity`}: {part.quantity} ·{' '}
                  {t`Quantity unit not supplied in this summary`}
                </Text>
              ))}
              <Text size='sm'>{t`Installed material state: Not recorded. Requirement, reservation, issue and use remain separate facts in Parts for this job.`}</Text>
              <Anchor
                component={Link}
                to={to('parts')}
                size='sm'
              >{t`Review material reservations, issues and recorded use`}</Anchor>
              <Anchor
                component={Link}
                to={to('verify')}
                size='sm'
              >{t`Review recorded verification results`}</Anchor>
              <Anchor
                component={Link}
                to={to('history')}
                size='sm'
              >{t`Open attributed Work history`}</Anchor>
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
        {workOrder.repair_packet && (
          <Accordion.Item value='gates'>
            <Accordion.Control>{t`Saved gate requirements (${workOrder.repair_packet.gates.length})`}</Accordion.Control>
            <Accordion.Panel>
              <Stack gap='sm'>
                {workOrder.repair_packet.gates.length === 0 ? (
                  <Text size='sm'>{t`No gate requirements returned from this packet. Physical permission is not established.`}</Text>
                ) : (
                  workOrder.repair_packet.gates.map((gate) => (
                    <Text key={gate.id} size='sm'>
                      {gate.name} · {gate.status} ·{' '}
                      {gate.is_blocking
                        ? t`Recorded as blocking`
                        : t`Not marked blocking in this record`}
                    </Text>
                  ))
                )}
                <Text size='sm'>{t`Gate requirements and recorded status do not replace verified physical conditions or an independent clearance.`}</Text>
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        )}
        <Accordion.Item value='energy'>
          <Accordion.Control>{t`Saved energy-control records`}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap='sm'>
              {!verification ? (
                <Text size='sm'>{t`Energy-control projection not supplied.`}</Text>
              ) : verification.lockouts.length === 0 ? (
                <Text size='sm'>{t`No lockout points returned for this Work. Current isolation and independent restrictions are not established.`}</Text>
              ) : (
                verification.lockouts.map((point) => (
                  <Paper key={point.id} withBorder p='xs'>
                    <Stack gap={4}>
                      <Text fw={600} size='sm'>
                        {point.device} · {point.energy_source} · {point.status}
                      </Text>
                      <Text size='sm'>
                        {t`Applied by`}: {point.applied_by ?? t`Not recorded`} ·{' '}
                        {t`Verified by`}: {point.verified_by ?? t`Not recorded`}
                      </Text>
                      <Text size='sm'>
                        {t`Verified at`}: {recordedTime(point.verified_at)} ·{' '}
                        {t`Restored at`}: {recordedTime(point.restored_at)}
                      </Text>
                      {point.note && <Text size='sm'>{point.note}</Text>}
                    </Stack>
                  </Paper>
                ))
              )}
              <Text size='sm'>{t`These are saved point records. Another crew’s restriction and job-wide work, test or operating authority remain separate.`}</Text>
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
        <Accordion.Item value='related'>
          <Accordion.Control>{t`Related work dependencies (${workOrder.dependencies.length} visible)`}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap='sm'>
              {workOrder.dependencies.length === 0 && (
                <Text size='sm'>{t`No visible dependency links returned. Shared-investigation membership and wider crew relationships are not supplied.`}</Text>
              )}
              {workOrder.dependencies.map((dependency) => (
                <Paper key={dependency.id} withBorder p='sm'>
                  <Stack gap={4}>
                    <Anchor
                      component={Link}
                      size='sm'
                      to={`/maintenance/work-orders/${dependency.card.id}/`}
                    >
                      {dependency.card.reference || `WO-${dependency.card.id}`}{' '}
                      · {dependency.card.title}
                    </Anchor>
                    <Text size='sm'>
                      {lifecycleLabel(dependency.card.lifecycle_status)} ·{' '}
                      {t`Work version`}:{' '}
                      {dependency.card.lifecycle_version ?? t`Not supplied`} ·{' '}
                      {t`Assignee`}:{' '}
                      {dependency.card.assigned_to_name || t`Not recorded`}
                    </Text>
                    <Text size='sm'>
                      {dependency.direction === 'predecessor'
                        ? t`Predecessor`
                        : t`Successor`}{' '}
                      · {dependency.dependency_type} · {t`Lag`}:{' '}
                      {dependency.lag_minutes} {t`min`}
                    </Text>
                  </Stack>
                </Paper>
              ))}
              <Text size='sm'>{t`These links are scheduling dependencies, not proof of a shared investigation or a concurrency agreement. Each Work keeps its own owner, evidence, permissions and lifecycle. Sharing evidence or resuming this Work does not complete sibling work or release another crew’s restriction.`}</Text>
              <ExecutionFact
                label={t`Cross-crew concurrency / clearance agreement`}
              >{t`Not recorded`}</ExecutionFact>
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
      <HoldResumeRecord workOrder={workOrder} />
    </ExecutionSection>
  );
}

/** Attributed hold/resume history supplements the existing native Activity. */
export function WorkContinuityHistory({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const value = workOrder.work_continuity;
  if (value?.rule !== 'work_continuity.v1') return null;
  return (
    <ExecutionSection
      title={t`Hold and resume record history`}
      testId='continuity-history'
    >
      {value.history.length === 0 ? (
        <Text size='sm'>{t`No native hold/resume events returned. This does not establish that physical conditions or shift ownership are unchanged.`}</Text>
      ) : (
        value.history.map((event) => (
          <Stack key={event.event_id} gap={3}>
            <Text size='sm' fw={600}>
              {event.action === 'hold' ? t`Hold recorded` : t`Resume recorded`}{' '}
              · {t`Work version`}: {event.lifecycle_version ?? t`Not recorded`}
            </Text>
            <Text size='sm'>{event.reason || t`Reason not recorded`}</Text>
            <Text size='sm'>
              {event.recorded_by || t`Recorder not recorded`} ·{' '}
              {recordedTime(event.recorded_at)}
            </Text>
            <Text size='sm' c='dimmed'>
              {lifecycleLabel(event.from_status)} →{' '}
              {lifecycleLabel(event.to_status)}
            </Text>
          </Stack>
        ))
      )}
      <Text
        size='sm'
        c='dimmed'
      >{t`Work record transitions do not record incoming acceptance or physical release.`}</Text>
    </ExecutionSection>
  );
}

/** Plan keeps actual request constraints visible without declaring the whole job blocked. */
export function WorkContinuityConstraints({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const [params] = useSearchParams();
  const requests = workOrder.investigation?.requests;
  const constrained =
    requests?.available && Array.isArray(requests.items)
      ? requests.items.filter((request) =>
          ['cannot_perform', 'pending_external', 'disputed'].includes(
            request.status
          )
        )
      : [];
  if (workOrder.lifecycle_status !== 'on_hold' && constrained.length === 0)
    return null;
  return (
    <Alert
      color='orange'
      title={t`Saved hold and request constraints`}
      data-testid='continuity-constraints'
    >
      <Stack gap='sm'>
        {workOrder.lifecycle_status === 'on_hold' && (
          <Text size='sm'>
            {t`Work hold`}:{' '}
            {workOrder.work_continuity?.current_hold?.reason ||
              t`Reason not supplied`}
          </Text>
        )}
        {constrained.map((request) => (
          <Stack key={request.id} gap={3}>
            <Text size='sm' fw={600}>
              {t`Request ${request.id}`} · {request.next_check}
            </Text>
            <Text size='sm'>
              {request.blocked_reason || t`Constraint reason not recorded`}
            </Text>
            <Text size='sm'>
              {t`Assignee`}: {request.owner_name || t`Not recorded`} ·{' '}
              {t`Checkpoint`}: {recordedTime(request.next_checkpoint)}
            </Text>
          </Stack>
        ))}
        <Text size='sm'>{t`These requests keep their own scope and unresolved status. Their relationship to other executable tasks or permissions is not recorded.`}</Text>
        <Anchor
          component={Link}
          size='sm'
          to={`?${workPanelParams(params, 'work')}`}
        >{t`Review continuity brief and pending owners`}</Anchor>
      </Stack>
    </Alert>
  );
}
