import type { TimeAssignment as Projection } from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { Accordion, SimpleGrid, Stack, Text } from '@mantine/core';
import {
  ExecutionFact,
  ExecutionSection,
  recordedTime
} from './ExecutionFacts';

function elapsed(value: number | null): string | null {
  if (value === null || !Number.isFinite(value) || value < 0) return null;
  return t`${Math.floor(value / 60)} min at last read`;
}

export function TimeAssignment({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const supplied = workOrder.time_assignment?.rule === 'time_assignment.v1';
  const value: Projection | undefined = supplied
    ? workOrder.time_assignment
    : undefined;
  return (
    <ExecutionSection
      title={t`Assignment and person-work time`}
      testId='time-assignment'
    >
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <ExecutionFact label={t`Recorded assignee`}>
          {value?.assigned_to ??
            workOrder.assigned_to_name ??
            workOrder.assigned_to_username ??
            null}
        </ExecutionFact>
        <ExecutionFact label={t`Legacy assignee label`}>
          {value?.legacy_assignee ?? workOrder.assignee}
        </ExecutionFact>
        <ExecutionFact label={t`Elapsed since recorded assignment`}>
          {elapsed(value?.elapsed_assignment_seconds ?? null)}
        </ExecutionFact>
        <ExecutionFact label={t`Assignment event at`}>
          {recordedTime(value?.assignment_event_at)}
        </ExecutionFact>
        <ExecutionFact label={t`Assignment recorded by`}>
          {value?.assignment_recorded_by}
        </ExecutionFact>
        <ExecutionFact label={t`Recorded assignment reason`}>
          {value?.assignment_reason}
        </ExecutionFact>
        <ExecutionFact label={t`Native actual start`}>
          {recordedTime(workOrder.actual_started_at)}
        </ExecutionFact>
        <ExecutionFact label={t`Native actual completion`}>
          {recordedTime(workOrder.actual_completed_at)}
        </ExecutionFact>
      </SimpleGrid>
      <Text
        size='sm'
        c='dimmed'
      >{t`Elapsed assignment is wall time at the last server read, not labor or accepted responsibility. Actual start and completion are job timestamps, not person-work intervals.`}</Text>
      <Text
        fw={600}
        size='sm'
      >{t`Attributed time: unavailable · coverage unknown`}</Text>
      <Text size='sm'>{t`No authoritative attributed intervals are supplied. Unclassified gaps and labor totals cannot be measured. Held work does not mean every person was waiting; capture duration and browser activity do not classify labor.`}</Text>
      <Text size='sm'>{t`Assignment factors, accepted handover and an attributed interval correction path are not supplied. No assignment score, accepted owner or corrected time is inferred.`}</Text>
      {value && (
        <Accordion variant='contained' keepMounted={false}>
          <Accordion.Item value='assignments'>
            <Accordion.Control>{t`Recorded assignment changes (${value.events.length})`}</Accordion.Control>
            <Accordion.Panel>
              <Stack gap='sm'>
                {value.events.length === 0 && (
                  <Text size='sm'>{t`No native assignment events returned. Assignment time and rationale remain unknown.`}</Text>
                )}
                {value.events.map((event) => (
                  <Stack gap={2} key={event.id}>
                    <Text size='sm'>
                      {event.reason ?? t`Assignment reason not recorded`}
                    </Text>
                    <Text size='xs' c='dimmed'>
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
                <Text
                  size='xs'
                  c='dimmed'
                >{t`Assignment changes are audit records, not accepted handovers or attributed work intervals.`}</Text>
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      )}
    </ExecutionSection>
  );
}
