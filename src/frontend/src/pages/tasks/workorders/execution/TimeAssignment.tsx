import type { TimeAssignment as Projection } from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { Accordion, Button, SimpleGrid, Stack, Text } from '@mantine/core';
import { StaffSummaryPopover } from '../../../../components/staff/StaffSummaryPopover';
import {
  ExecutionFact,
  ExecutionSection,
  recordedTime
} from './ExecutionFacts';
import { RecordedWorkTime } from './RecordedWorkTime';

function elapsed(value: number | null): string | null {
  if (value === null || !Number.isFinite(value) || value < 0) return null;
  return t`${Math.floor(value / 60)} min at last read`;
}

export function TimeAssignment({
  workOrder,
  onRecoveryNeeded,
  onAssign
}: Readonly<{
  workOrder: WorkOrderOverview;
  onRecoveryNeeded?: () => void;
  onAssign?: () => void;
}>) {
  const supplied = workOrder.time_assignment?.rule === 'time_assignment.v1';
  const value: Projection | undefined = supplied
    ? workOrder.time_assignment
    : undefined;
  const handover = workOrder.work_continuity?.accepted_handover?.available
    ? workOrder.work_continuity.accepted_handover
    : value?.accepted_handover;
  return (
    <ExecutionSection
      title={t`Assignment and person-work time`}
      testId='time-assignment'
      info={t`Elapsed assignment is wall time at the last read. Job timestamps are not person-work intervals or accepted responsibility. Assignment factors and handover acceptance require separate records.`}
    >
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        {(workOrder.assigned_to != null ||
          value?.assigned_to ||
          workOrder.assigned_to_name ||
          workOrder.assigned_to_username ||
          onAssign) && (
          <ExecutionFact label={t`Recorded assignee`}>
            <Stack gap='xs'>
              <StaffSummaryPopover
                userId={workOrder.assigned_to}
                label={
                  value?.assigned_to ??
                  workOrder.assigned_to_name ??
                  workOrder.assigned_to_username
                }
                scope={{ workOrderId: workOrder.id }}
              />
              {onAssign && (
                <Button
                  size='compact-sm'
                  variant='light'
                  onClick={onAssign}
                >{t`Assign work order`}</Button>
              )}
            </Stack>
          </ExecutionFact>
        )}
        {[
          [t`Assignee label`, value?.legacy_assignee ?? workOrder.assignee],
          [
            t`Elapsed since recorded assignment`,
            elapsed(value?.elapsed_assignment_seconds ?? null)
          ],
          [
            t`Assignment event at`,
            value?.assignment_event_at
              ? recordedTime(value.assignment_event_at)
              : null
          ],
          // Attribution and rationale stay visible as Not recorded when absent.
          [t`Assignment recorded by`, value?.assignment_recorded_by, true],
          [t`Recorded assignment reason`, value?.assignment_reason, true],
          [
            t`Started at`,
            workOrder.actual_started_at
              ? recordedTime(workOrder.actual_started_at)
              : null
          ],
          [
            t`Completed at`,
            workOrder.actual_completed_at
              ? recordedTime(workOrder.actual_completed_at)
              : null
          ]
        ]
          .filter(([, fact, always]) => always || (fact != null && fact !== ''))
          .map(([label, fact]) => (
            <ExecutionFact key={String(label)} label={String(label)}>
              {fact as string | null | undefined}
            </ExecutionFact>
          ))}
      </SimpleGrid>

      <RecordedWorkTime
        key={workOrder.id}
        workOrder={workOrder}
        onRecoveryNeeded={onRecoveryNeeded}
      />
      {handover?.available ? (
        <Stack gap={2} data-testid='time-handover-record'>
          <Text size='sm'>
            {handover.state === 'accepted'
              ? t`Incoming handover acceptance is recorded.`
              : t`Current incoming handover acceptance is not recorded.`}
          </Text>
          <Text size='sm'>
            {t`Responsible owner`}:{' '}
            {handover.responsible_owner || t`Not recorded`}
          </Text>
          {handover.state === 'accepted' && (
            <Text size='sm'>
              {handover.accepted_by} · {recordedTime(handover.accepted_at)} · v
              {handover.accepted_version}
            </Text>
          )}
        </Stack>
      ) : null}
      {value && (
        <Accordion variant='contained' keepMounted={false}>
          <Accordion.Item value='assignments'>
            <Accordion.Control>{t`Recorded assignment changes (${value.events.length})`}</Accordion.Control>
            <Accordion.Panel>
              <Stack gap='sm'>
                {value.events.length === 0 && (
                  <Text size='sm'>{t`No assignment changes recorded`}</Text>
                )}
                {value.events.map((event) => (
                  <Stack gap={2} key={event.id}>
                    <Text size='sm'>
                      {event.reason ?? t`Assignment reason not recorded`}
                    </Text>
                    <Text size='xs'>
                      {event.recorded_by ?? t`Recorder not recorded`} ·{' '}
                      {recordedTime(event.recorded_at)}
                    </Text>
                    <Text size='xs'>
                      {t`Assignee recorded in this event`}:{' '}
                      {event.assigned_to_name ?? t`Not recorded`}
                    </Text>
                    <Text size='xs'>
                      {t`Previous assignee recorded in this event`}:{' '}
                      {event.previous_assigned_to_name ?? t`Not recorded`}
                    </Text>
                  </Stack>
                ))}
                <Text size='xs'>{t`Assignment changes are audit records, not accepted handovers or attributed work intervals.`}</Text>
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
    </ExecutionSection>
  );
}
