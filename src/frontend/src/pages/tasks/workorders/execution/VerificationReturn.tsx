import type { VerificationTest } from '@lib/types/WorkExecution';
import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Accordion,
  Alert,
  Badge,
  Group,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';
import { CloseoutLink } from '../CloseoutLink';
import {
  ExecutionFact,
  ExecutionSection,
  executionBadgeStyles,
  recordedTime
} from './ExecutionFacts';
import { StepCriteria } from './PreparedPackage';
import { ServiceRestoration } from './ServiceRestoration';
import { recordedValue, testEvaluation } from './verificationProjection';

function TestResult({ test }: Readonly<{ test: VerificationTest }>) {
  const evaluation = testEvaluation(test);
  const label =
    evaluation === 'failed'
      ? t`Failed — recorded result`
      : evaluation === 'recorded_pass'
        ? t`Recorded pass — acceptance separate`
        : evaluation === 'not_applicable'
          ? t`Recorded not applicable`
          : t`Not evaluated`;
  return (
    <Stack gap='sm' data-testid={`verification-test-${test.id}`}>
      <Group justify='space-between' align='flex-start'>
        <Text size='sm' fw={600}>
          {test.step.title}
        </Text>
        <Badge
          styles={executionBadgeStyles}
          color={
            evaluation === 'failed'
              ? 'red'
              : evaluation === 'recorded_pass'
                ? 'blue'
                : 'gray'
          }
        >
          {label}
        </Badge>
      </Group>
      <StepCriteria step={test.step} />
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <ExecutionFact label={t`Actual recorded value`}>
          {recordedValue(test.value) === null
            ? null
            : `${recordedValue(test.value)} ${test.step.unit}`.trim()}
        </ExecutionFact>
        <ExecutionFact label={t`Result recorded by`}>
          {test.recorded_by}
        </ExecutionFact>
        <ExecutionFact label={t`Result recorded at`}>
          {recordedTime(test.recorded_at)}
        </ExecutionFact>
        <ExecutionFact
          label={t`Applied revision / result version`}
        >{`${test.procedure_revision ?? t`Not recorded`} / ${test.version}`}</ExecutionFact>
        <ExecutionFact label={t`Recorded step status`}>
          {test.status.replaceAll('_', ' ')}
        </ExecutionFact>
        <ExecutionFact label={t`Conditions and instruments`}>
          {null}
        </ExecutionFact>
      </SimpleGrid>
      {evaluation === 'not_evaluated' && (
        <Text size='sm'>{t`The saved result does not establish a pass against applicable criteria. A completed step or an unbounded numeric check is not accepted equipment verification.`}</Text>
      )}
      {(test.note || test.disposition_reason) && (
        <Text size='sm'>{test.disposition_reason || test.note}</Text>
      )}
      <Accordion variant='contained'>
        <Accordion.Item value='attempts'>
          <Accordion.Control>{t`Retained attempt events (${test.events.length})`}</Accordion.Control>
          <Accordion.Panel>
            <Stack gap='sm'>
              <Text size='sm'>{t`Earlier outcomes are retained. Previous values and complete attempt conditions were not retained by the native writer; the current reading is never substituted for them.`}</Text>
              {test.events.map((event) => (
                <Stack key={event.id} gap={2}>
                  <Text size='sm' fw={600}>
                    {event.event_type === 'STEP_FAILED'
                      ? t`Failed attempt retained`
                      : event.event_type === 'STEP_REOPENED'
                        ? t`Reopened for correction or retest`
                        : event.event_type === 'STEP_NOT_APPLICABLE'
                          ? t`Not-applicable disposition`
                          : t`Step completion recorded`}
                  </Text>
                  <Text size='xs' c='dimmed'>
                    {event.recorded_by ?? t`Recorder not recorded`} ·{' '}
                    {recordedTime(event.recorded_at)} · {t`Result version`}{' '}
                    {event.version ?? t`Not recorded`}
                  </Text>
                  {event.reason && <Text size='sm'>{event.reason}</Text>}
                  <Text size='sm'>{t`Historical value: not available`}</Text>
                  {event.evidence_ids.length > 0 && (
                    <Text size='sm'>
                      {t`Recorded evidence references`}:{' '}
                      {event.evidence_ids.join(', ')}
                    </Text>
                  )}
                </Stack>
              ))}
            </Stack>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
    </Stack>
  );
}

export function VerificationReturn({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const value = workOrder.verification_return;
  const supplied = value?.rule === 'verification_return.v1';
  return (
    <Stack gap='md' data-testid='verification-return'>
      <CloseoutLink />
      <ExecutionSection title={t`Physical verification`}>
        {!supplied ? (
          <Text size='sm'>{t`Physical test criteria and attempts are not supplied by this server. Not evaluated.`}</Text>
        ) : value.procedures_state !== 'read' ? (
          <Text size='sm'>{t`Governed test records are not enabled. Not evaluated.`}</Text>
        ) : value.tests.length === 0 ? (
          <Text size='sm'>{t`No procedure test records returned. Applicable criteria and physical verification are not established.`}</Text>
        ) : (
          value.tests.map((test) => <TestResult key={test.id} test={test} />)
        )}
        <ExecutionFact
          label={t`Responsible technical acceptance`}
        >{t`Not available — no equipment-test acceptance record is supplied.`}</ExecutionFact>
      </ExecutionSection>
      <ExecutionSection title={t`Controlled test permission`}>
        <Text size='sm'>{t`Awaiting test authority — authority, permitted test scope, conditions, owner and effective time are not supplied.`}</Text>
        <Text
          size='sm'
          c='dimmed'
        >{t`Permission for a controlled test does not permit unrestricted operation.`}</Text>
      </ExecutionSection>
      <ExecutionSection title={t`Release and operating return`}>
        {supplied && value.lockouts.length > 0 ? (
          value.lockouts.map((point) => (
            <Stack key={point.id} gap={4}>
              <Text size='sm' fw={600}>
                {point.device} · {point.status.replaceAll('_', ' ')}
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
          ))
        ) : (
          <Text size='sm'>{t`Awaiting release record — no lockout-point records supplied here.`}</Text>
        )}
        <Text
          size='sm'
          c='dimmed'
        >{t`A restored lockout point is one source record. It does not clear independent restrictions or establish job-wide release.`}</Text>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <ExecutionFact
            label={t`Operating permission`}
          >{t`Not recorded — awaiting operating authority.`}</ExecutionFact>
          <ExecutionFact
            label={t`Observed operating return`}
          >{t`Awaiting operations record of return.`}</ExecutionFact>
        </SimpleGrid>
      </ExecutionSection>
      <ServiceRestoration workOrder={workOrder} />
      <ExecutionSection title={t`Recorded verification`}>
        <ExecutionFact label={t`Verification summary`}>
          {workOrder.structured_closeout?.verification_summary}
        </ExecutionFact>
        <ExecutionFact label={t`Recorded verification time`}>
          {recordedTime(workOrder.structured_closeout?.verified_at)}
        </ExecutionFact>
        <Alert color='gray'>{t`A recorded summary or completed work order does not establish permission to operate.`}</Alert>
      </ExecutionSection>
    </Stack>
  );
}
