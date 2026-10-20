import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Group,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';
import { Link, useSearchParams } from 'react-router-dom';
import { type WorkPanel, workPanelParams } from '../workPanelState';
import { ContinueInvestigation } from './ContinueInvestigation';
import {
  ExecutionFact,
  ExecutionSection,
  executionBadgeStyles,
  recordedTime
} from './ExecutionFacts';
import { HandledByAimms } from './HandledByAimms';
import { AuthorityRecords, currentAuthority } from './OperationalAuthority';
import { workBrief } from './workBriefProjection';

/** Outcome-led case brief; each owning panel retains its native operations. */
export function WorkBrief({
  workOrder: work
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const [params] = useSearchParams();
  const brief = workBrief(work);
  const account = work.structured_closeout;
  const routine = ['preventive', 'inspection', 'calibration'].includes(
    work.work_order_type
  );
  const explanation = brief.investigation?.explanation;
  const link = (panel: WorkPanel, label: string) => (
    <Anchor
      component={Link}
      to={{ search: `?${workPanelParams(params, panel)}` }}
      size='sm'
    >
      {label}
    </Anchor>
  );
  return (
    <Stack gap='md' data-testid='work-brief'>
      <ExecutionSection
        title={
          account
            ? routine
              ? t`Recorded maintenance outcome`
              : t`Recorded repair outcome`
            : t`Current work outcome`
        }
      >
        {account ? (
          <>
            <Text fw={600}>{account.result || t`Result not recorded`}</Text>
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <ExecutionFact label={t`Recorded intervention`}>
                {account.action}
              </ExecutionFact>
              <ExecutionFact label={t`Verification account`}>
                {account.verification_summary}
              </ExecutionFact>
              <ExecutionFact
                label={t`Maintenance account source`}
              >{t`Closeout ${account.id} · ${recordedTime(account.completed_at)}`}</ExecutionFact>
              <ExecutionFact label={t`Recorded by`}>
                {account.completed_by == null
                  ? null
                  : t`User ${account.completed_by}`}
              </ExecutionFact>
            </SimpleGrid>
            {account.amended && (
              <Text size='sm'>{t`Amended account · ${account.amendment_count} retained amendments`}</Text>
            )}
            {account.follow_up_required && (
              <Alert color='orange' title={t`Follow-up remains open`}>
                {account.follow_up || t`Follow-up details not recorded`}
              </Alert>
            )}
          </>
        ) : work.maintenance_record ? (
          <>
            <Text fw={600}>{work.maintenance_record.summary}</Text>
            <Text size='sm'>
              {t`Maintenance record ${work.maintenance_record.id}`} ·{' '}
              {work.maintenance_record.performed_by || t`Recorder not recorded`}{' '}
              · {recordedTime(work.maintenance_record.date)}
            </Text>
          </>
        ) : (
          <Text size='sm'>
            {routine
              ? t`No recorded maintenance outcome is supplied. The lifecycle and Board stage do not establish a physical result.`
              : t`No recorded repair outcome is supplied. The lifecycle and Board stage do not establish a physical result.`}
          </Text>
        )}
        {link('closeout', t`View maintenance account and report`)}
      </ExecutionSection>

      {brief.next && (
        <Alert
          color={brief.next.blocked_reason ? 'orange' : 'blue'}
          title={t`Next investigation decision · Request ${brief.next.id}`}
        >
          <Stack gap='xs'>
            <Text size='sm' fw={600}>
              {brief.next.next_check}
            </Text>
            <Text size='sm'>{brief.next.purpose}</Text>
            <Text size='sm'>
              {t`Owner`}: {brief.next.owner_name || t`Not recorded`} ·{' '}
              {t`Checkpoint`}: {recordedTime(brief.next.next_checkpoint)}
            </Text>
            {brief.next.blocked_reason && (
              <Text size='sm'>{brief.next.blocked_reason}</Text>
            )}
            <ContinueInvestigation
              workOrder={work}
              panel='work'
              request={brief.next}
              caption={false}
            />
          </Stack>
        </Alert>
      )}

      <ExecutionSection title={t`Service and equipment state`}>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <ExecutionFact
            label={t`Condition controlled`}
          >{t`Not available — no condition-control record supplied`}</ExecutionFact>
          <ExecutionFact
            label={t`Required service restored`}
          >{t`Not available — service restoration not supplied`}</ExecutionFact>
          <ExecutionFact label={t`Technical test acceptance`}>
            {work.operational_authority ? (
              <AuthorityRecords
                value={work.operational_authority}
                kinds={['test_acceptance']}
              />
            ) : (
              t`Not available — recorded test results are separate`
            )}
          </ExecutionFact>
          <ExecutionFact label={t`Operating return and permission`}>
            {work.operational_authority ? (
              <AuthorityRecords
                value={work.operational_authority}
                kinds={['operating_permission', 'observed_return']}
              />
            ) : (
              t`Not available — no operating-authority record`
            )}
          </ExecutionFact>
          <ExecutionFact label={t`Standby capacity / resilience`}>
            {work.operational_authority ? (
              <AuthorityRecords
                value={work.operational_authority}
                kinds={['service_state']}
              />
            ) : (
              t`Not recorded`
            )}
          </ExecutionFact>
          <ExecutionFact label={t`Operating restrictions and duty`}>
            {currentAuthority(work.operational_authority, 'service_state')
              .length
              ? t`See the attributed duty and standby records above`
              : t`Not recorded`}
          </ExecutionFact>
        </SimpleGrid>
        <Text
          size='sm'
          c='dimmed'
        >{t`A completed maintenance record does not establish service restoration, physical readiness or closed obligations.`}</Text>
        {link('verify', t`View verification and operating-return records`)}
      </ExecutionSection>

      <HandledByAimms value={work.handled_actions} />
      <ExecutionSection title={t`Shared investigation summary`}>
        {explanation?.likely_cause ? (
          <Stack gap={4}>
            <Badge
              styles={executionBadgeStyles}
              color={explanation.preliminary ? 'orange' : 'blue'}
              variant='light'
            >
              {explanation.preliminary
                ? t`Preliminary — not technician verified`
                : t`Technician verification recorded`}
            </Badge>
            <Text size='sm'>{explanation.likely_cause}</Text>
            <Text size='xs' c='dimmed'>
              {brief.investigation?.packet?.reference} ·{' '}
              {explanation.verified_by || t`Verifier not recorded`} ·{' '}
              {recordedTime(
                explanation.verified_at || explanation.generated_at
              )}
            </Text>
          </Stack>
        ) : (
          <Text size='sm'>{t`No saved explanation supplied. Routine inspection, calibration or preventive work does not require an invented fault cause.`}</Text>
        )}
        {brief.unresolved === null ? (
          <Text size='sm'>{t`Saved request queue not supplied`}</Text>
        ) : (
          <Text size='sm'>{t`${brief.unresolved.length} unresolved requests · ${brief.requests!.length} saved requests`}</Text>
        )}
        {brief.unresolved?.map((request) => (
          <Text size='sm' key={request.id}>
            {t`Request ${request.id}`} · {request.status.replaceAll('_', ' ')} ·{' '}
            {request.next_check}
            {request.blocked_reason ? ` · ${request.blocked_reason}` : ''}
          </Text>
        ))}
        {brief.investigation?.findings
          .filter((f) => f.verification === 'disputed')
          .map((finding) => (
            <Alert
              key={finding.id}
              color='orange'
              title={t`Disputed finding ${finding.id}`}
            >
              {finding.observation} ·{' '}
              {finding.recorded_by || t`Recorder not recorded`} ·{' '}
              {recordedTime(finding.updated_at)}
            </Alert>
          ))}
        {link('diagnose', t`View requests, evidence and corrections`)}
      </ExecutionSection>

      <ExecutionSection title={t`Prepared work and recorded checks`}>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <ExecutionFact label={t`Current approved scope`}>
            {brief.scope
              ? t`Scope ${brief.scope.id} · revision ${brief.scope.version} · ${brief.scope.approved_by || t`Approver not recorded`} · ${recordedTime(brief.scope.approved_at)}`
              : t`Not supplied as one current approved revision`}
          </ExecutionFact>
          <ExecutionFact label={t`Recorded physical checks`}>
            {brief.tests
              ? t`${brief.passed!.length} recorded passes · ${brief.failed!.length} failures · ${brief.tests.length} test records; acceptance separate`
              : t`Test records not supplied or not enabled`}
          </ExecutionFact>
        </SimpleGrid>
        {work.parts.length > 0 ? (
          work.parts.slice(0, 3).map((part) => (
            <Text size='sm' key={part.id}>
              {part.part_name} · {t`Required`}: {part.quantity} · {t`Allocated`}
              : {part.allocated_quantity}
              {' · '}
              {t`Requirement ${part.id}`} · {recordedTime(part.updated_at)}
            </Text>
          ))
        ) : (
          <Text size='sm'>{t`No material requirements returned in this overview. Reservation, issue and use records remain in Parts.`}</Text>
        )}
        {work.parts.length > 3 && (
          <Text size='sm'>{t`${work.parts.length} material requirements recorded; all records in Parts`}</Text>
        )}
        <Group gap='md'>
          {link('plan', t`View package, safety and dependencies`)}
          {link('parts', t`View required, reserved and used materials`)}
        </Group>
        {work.dependencies.length > 0 && (
          <Text size='sm'>{t`${work.dependencies.length} related-work dependencies recorded. Each retains its own lifecycle and scope.`}</Text>
        )}
      </ExecutionSection>
    </Stack>
  );
}
