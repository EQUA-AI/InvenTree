import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Group,
  Stack,
  Text
} from '@mantine/core';
import { Link, useSearchParams } from 'react-router-dom';
import { workPanelParams } from '../workPanelState';
import { ContinueInvestigation } from './ContinueInvestigation';
import {
  ExecutionSection,
  executionBadgeStyles,
  recordedTime
} from './ExecutionFacts';
import { StepCriteria } from './PreparedPackage';
import { RoutineFindingForm } from './RoutineFindingForm';
import { RoutineLinks } from './RoutineLinks';
import { recordedValue } from './verificationProjection';

/** Type-specific records inside Work; the native readers and commands still own them. */
export function RoutineWork({
  workOrder: work
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const [params] = useSearchParams();
  const title = (
    {
      preventive: t`Preventive tasks and readings`,
      inspection: t`Inspection tasks and readings`,
      calibration: t`Calibration tasks and readings`
    } as Record<string, string>
  )[work.work_order_type];
  if (!title) return null;
  const value =
    work.routine_work?.work_order_type === work.work_order_type
      ? work.routine_work
      : null;
  const link = (
    panel: 'plan' | 'verify' | 'closeout' | 'diagnose',
    label: string
  ) => (
    <Anchor
      component={Link}
      to={{ search: `?${workPanelParams(params, panel)}` }}
      size='sm'
    >
      {label}
    </Anchor>
  );
  const requests = work.investigation?.requests;
  const selected = requests?.available
    ? requests.items?.find(
        (r) =>
          r.work_order === work.id &&
          String(r.id) === params.get('investigation_request')
      )
    : undefined;
  return (
    <ExecutionSection title={title} testId='routine-work'>
      <Text size='sm'>{t`Tasks, readings and completion belong to this original Work. Investigate a fault only when needed.`}</Text>
      {!value ? (
        <Alert
          color='gray'
          title={t`Routine records unavailable`}
        >{t`This server does not supply the routine task and reading projection. Open Plan & Safety and Verify & Return for the available native records.`}</Alert>
      ) : (
        <>
          {value.procedures_state !== 'read' ? (
            <Text size='sm'>{t`Procedure records are not enabled.`}</Text>
          ) : value.applications.length === 0 ? (
            <Text size='sm'>{t`No applied procedure returned. Required tasks and their completion are not established.`}</Text>
          ) : (
            value.applications.map((application) => (
              <Stack key={application.id} gap='xs'>
                <Text
                  fw={600}
                  size='sm'
                >{t`Application ${application.id} · revision ${application.revision ?? t`Not recorded`}`}</Text>
                <Text size='xs' c='dimmed'>
                  {application.applied_by ?? t`Recorder not recorded`} ·{' '}
                  {recordedTime(application.applied_at)}
                </Text>
                <Accordion variant='contained'>
                  {application.tasks.map((task) => (
                    <Accordion.Item key={task.id} value={String(task.id)}>
                      <Accordion.Control>
                        <Group gap='xs' wrap='wrap'>
                          <Text size='sm' fw={500}>
                            {task.step.sequence}.{' '}
                            {task.step.title || t`Task title not recorded`}
                          </Text>
                          <Badge
                            color={task.status === 'failed' ? 'red' : 'gray'}
                            styles={executionBadgeStyles}
                          >
                            {task.status.replaceAll('_', ' ')}
                          </Badge>
                        </Group>
                      </Accordion.Control>
                      <Accordion.Panel>
                        <Stack gap='xs'>
                          <Text size='xs'>
                            {task.step.required
                              ? t`Required by applied procedure`
                              : t`Optional in applied procedure`}
                          </Text>
                          <StepCriteria step={task.step} />
                          <Text size='sm'>
                            {t`Recorded value`}:{' '}
                            {task.status === 'not_applicable'
                              ? t`Not applicable — prior value unavailable`
                              : (recordedValue(task.value) ?? t`Not recorded`)}
                          </Text>
                          <Text size='xs'>
                            {task.recorded_by ?? t`Recorder not recorded`} ·{' '}
                            {recordedTime(task.recorded_at)} ·{' '}
                            {t`Result version ${task.version}`}
                          </Text>
                          {(task.disposition_reason || task.note) && (
                            <Text size='sm'>
                              {task.status === 'not_applicable'
                                ? task.disposition_reason
                                : task.note}
                            </Text>
                          )}
                          <Text size='xs' c='dimmed'>
                            {t`Step reference`}: {task.step.key} · {t`Snapshot`}
                            : {application.snapshot_hash || t`Not recorded`}
                          </Text>
                        </Stack>
                      </Accordion.Panel>
                    </Accordion.Item>
                  ))}
                </Accordion>
                {application.tasks.length === 0 && (
                  <Text size='sm'>{t`No execution records returned for this application.`}</Text>
                )}
              </Stack>
            ))
          )}
          <Text size='sm' fw={600}>{t`Saved readings`}</Text>
          {value.readings_state !== 'read' ? (
            <Text size='sm'>{t`Closeout reading records are not enabled.`}</Text>
          ) : value.readings.length === 0 ? (
            <Text size='sm'>{t`No closeout readings returned. This does not establish that required measurements were taken.`}</Text>
          ) : (
            value.readings.map((reading) => (
              <Stack
                key={reading.id}
                gap={2}
                component='section'
                aria-label={t`Saved reading ${reading.id}`}
              >
                <Text size='sm' fw={500}>
                  {reading.label} ·{' '}
                  {reading.phase === 'before'
                    ? t`Before`
                    : reading.phase === 'after'
                      ? t`After`
                      : t`Phase not recorded`}
                </Text>
                <Text size='sm'>
                  {reading.value ?? t`Normalized value not recorded`}{' '}
                  {reading.unit || t`Unit not recorded`} ·{' '}
                  {reading.verification_state === 'verified' &&
                  reading.expected_min == null &&
                  reading.expected_max == null
                    ? t`Value parsed · no limits recorded · not evaluated`
                    : reading.verification_state.replaceAll('_', ' ')}
                </Text>
                <Text size='sm'>
                  {t`Original entry`}: {reading.raw_text || t`Not recorded`}
                </Text>
                <Text size='xs'>
                  {t`Recorded limits`}:{' '}
                  {reading.expected_min ?? t`Not recorded`} –{' '}
                  {reading.expected_max ?? t`Not recorded`} ·{' '}
                  {t`Reading ${reading.id}`} · {t`Step execution`}:{' '}
                  {reading.step_execution ?? t`Not recorded`}
                </Text>
                {reading.warnings.map((warning, index) => (
                  <Text size='sm' key={`${index}-${warning}`}>
                    {warning}
                  </Text>
                ))}
                {(reading.verification_state === 'dispositioned' ||
                  reading.disposition_reason) && (
                  <Stack
                    gap={2}
                    component='section'
                    aria-label={t`Reading ${reading.id} disposition`}
                  >
                    <Text size='sm'>
                      {t`Disposition reason`}:{' '}
                      {reading.disposition_reason || t`Not recorded`}
                    </Text>
                    <Text size='xs'>{t`Disposition recorded by: Not recorded`}</Text>
                    <Text size='xs'>{t`Disposition recorded at: Not recorded`}</Text>
                    {reading.verification_state === 'dispositioned' && (
                      <Text size='xs'>{t`Earlier verification outcome is not retained in this reading record.`}</Text>
                    )}
                  </Stack>
                )}
                <Text size='xs' c='dimmed'>
                  {t`Reading recorded by`}:{' '}
                  {reading.recorded_by_name ?? t`Not recorded`} ·{' '}
                  {t`Reading recorded at`}: {recordedTime(reading.recorded_at)}
                </Text>
              </Stack>
            ))
          )}
        </>
      )}
      {work.work_order_type === 'calibration' && (
        <Alert color='gray' title={t`Calibration traceability incomplete`}>
          <Text size='sm'>{t`As-found/as-left designation, reference instrument identity, certificate, calibration validity and test conditions are not supplied.`}</Text>
          {value?.readings_state === 'read' && value.readings.length > 0 && (
            <Text size='sm'>{t`Returned before/after readings and any recorded limits do not establish calibration acceptance.`}</Text>
          )}
        </Alert>
      )}
      <Group gap='md'>
        {link('plan', t`View applied references and authority`)}
        {link('verify', t`Open readings and test records`)}
        {link('closeout', t`Open native completion`)}
      </Group>
      <Text
        size='xs'
        c='dimmed'
      >{t`Applied instructions and resource references remain in Plan & Safety. Original source documents and qualified physical authority are not established by a software permission or completed task. Native site readiness still applies at completion.`}</Text>
      <Accordion variant='contained'>
        <Accordion.Item value='fault'>
          <Accordion.Control>{t`Fault found during this work`}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap='sm'>
              <RoutineLinks workOrder={work} />
              <RoutineFindingForm workOrder={work} />
              <ContinueInvestigation
                workOrder={work}
                panel='work'
                request={selected}
                label={
                  selected
                    ? t`Discuss request ${selected.id}`
                    : t`Discuss fault in existing assistant`
                }
                caption={false}
                variant='light'
              />
              {link('diagnose', t`Open saved investigation requests`)}
              <Text size='sm'>{t`Discussion retains this Work as context. A request or evidence is saved only through the existing approved chat proposal. Opening chat does not change the original tasks, evidence or completion.`}</Text>
              <Text size='sm'>{t`Linked corrective Work is created only by an explicit command. The routine Work retains its tasks, readings and outcome.`}</Text>
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </ExecutionSection>
  );
}
