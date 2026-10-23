import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { Anchor, Badge, SimpleGrid, Stack, Text } from '@mantine/core';
import { Link, useSearchParams } from 'react-router-dom';
import { MissingValue } from '../../../../components/items/ValueTooltip';
import { workPanelParams } from '../workPanelState';
import {
  ExecutionFact,
  ExecutionSection,
  recordedTime
} from './ExecutionFacts';
import { AuthorityRecorder, AuthorityRecords } from './OperationalAuthority';
import { acceptanceSummary } from './authorityProjection';
import { authorityReading } from './authorityReading';
import { testEvaluation } from './verificationProjection';

/** Existing Work facts only; record completion never promotes a service state. */
export function ServiceRestoration({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const [params] = useSearchParams();
  const requestLink = (id: number) => {
    const next = workPanelParams(params, 'diagnose');
    next.set('investigation_request', String(id));
    return `?${next}`;
  };
  const authority = workOrder.operational_authority;
  const acceptance = acceptanceSummary(authority);
  const acceptanceBadge =
    acceptance.state === 'unavailable'
      ? t`Test acceptance: unavailable`
      : acceptance.state === 'not_recorded'
        ? t`Test acceptance: not recorded`
        : t`Test acceptance: ${acceptance.outcomes.join(', ')}${acceptance.basis ? t` · basis changed or unavailable` : ''}${acceptance.ineffective ? t` · outside effective window` : ''}`;
  const closeout = workOrder.structured_closeout;
  const verification = workOrder.verification_return;
  const tests =
    verification?.rule === 'verification_return.v1' &&
    verification.procedures_state === 'read'
      ? verification.tests
      : undefined;
  const requests = workOrder.investigation?.requests;
  const unresolved =
    requests?.available && Array.isArray(requests.items)
      ? requests.items.filter((row) => row.status !== 'resolved')
      : undefined;
  return (
    <ExecutionSection
      title={t`Service restoration and remaining obligations`}
      testId='service-restoration'
      info={[
        t`Work completion, test results and telemetry are separate from test acceptance, operating permission, service capacity and remaining obligations. Review each attributed record and its effective window.`,
        ...(closeout?.follow_up_required
          ? [
              t`Follow-up owner, original trigger, checkpoint and resolution are not supplied. The maintenance update does not resolve this follow-up.`
            ]
          : [])
      ].join(' ')}
    >
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <ExecutionFact label={t`Work status`}>
          {workOrder.lifecycle_status?.replaceAll('_', ' ') || t`Unavailable`}
        </ExecutionFact>
        <ExecutionFact label={t`Completed at`}>
          {recordedTime(workOrder.actual_completed_at)}
        </ExecutionFact>
        <ExecutionFact label={t`Maintenance record`}>
          {closeout ? (
            t`Updated · Closeout ${closeout.id}`
          ) : (
            <MissingValue reason={t`No completed maintenance account`} />
          )}
        </ExecutionFact>
        <ExecutionFact label={t`Saved physical test outcomes`}>
          {tests ? (
            t`Recorded passes: ${tests.filter((row) => testEvaluation(row) === 'recorded_pass').length} · Current failures: ${tests.filter((row) => testEvaluation(row) === 'failed').length}`
          ) : (
            <MissingValue reason={t`Test records unavailable`} />
          )}
        </ExecutionFact>
      </SimpleGrid>
      {closeout && (
        <Text size='xs'>
          {t`Completed account source`}: {closeout.id} ·{' '}
          {closeout.completed_by_name || t`Recorder unavailable`} ·{' '}
          {recordedTime(closeout.completed_at)}
        </Text>
      )}

      <ExecutionFact label={t`Test acceptance`}>
        {['unavailable', 'not_recorded'].includes(acceptance.state) ? (
          <MissingValue reason={acceptanceBadge} />
        ) : (
          <Badge
            styles={{
              root: { textTransform: 'none', height: 'auto' },
              label: { whiteSpace: 'normal' }
            }}
            color='gray'
            variant='light'
          >
            {acceptanceBadge}
          </Badge>
        )}
      </ExecutionFact>
      {authority ? (
        <>
          <AuthorityRecords
            value={authority}
            kinds={[
              'test_acceptance',
              'operating_permission',
              'observed_return',
              'service_state'
            ]}
          />

          {authority.telemetry.length ? (
            <Stack gap='xs'>
              <Text
                size='sm'
                fw={600}
              >{t`Read-only operating telemetry context`}</Text>
              {authority.telemetry.map((row) => (
                <Text
                  key={row.binding_id}
                  size='xs'
                  style={{ overflowWrap: 'anywhere' }}
                >
                  {row.target_name} · {row.tag} ·{' '}
                  {authorityReading(row.value) ?? t`Not recorded`} ·{' '}
                  {row.source} · {recordedTime(row.observed_at)} · {row.quality}
                  {row.stale ? t` · Stale` : ''}
                  {!row.source_active ? t` · Source inactive` : ''}
                </Text>
              ))}
            </Stack>
          ) : (
            <MissingValue
              reason={t`Duty / standby / run telemetry: Not recorded — no reviewed bound tags supplied.`}
            />
          )}
          <AuthorityRecorder value={authority} mode='service' />
        </>
      ) : null}

      <Stack gap='xs'>
        <Text
          size='sm'
          fw={600}
        >{t`Recorded follow-up and unresolved requests`}</Text>
        {closeout &&
          (closeout.follow_up_required || closeout.follow_up?.trim()) && (
            <Stack gap={2}>
              <Text size='sm'>
                {closeout.follow_up ||
                  t`Follow-up required; detail unavailable`}
              </Text>
              <Text size='xs'>
                {t`Source`}: {t`Closeout`} {closeout.id} · {t`Saved at`}:{' '}
                {recordedTime(closeout.completed_at)}
              </Text>
            </Stack>
          )}
        {unresolved === undefined ? (
          <MissingValue
            reason={t`Saved request queue unavailable; remaining requests are unknown.`}
          />
        ) : unresolved.length === 0 ? (
          <Text size='sm'>{t`No open investigation requests`}</Text>
        ) : (
          unresolved.map((row) => (
            <Stack key={row.id} gap={2}>
              <Anchor
                component={Link}
                replace
                to={requestLink(row.id)}
                size='sm'
              >
                {t`Request ${row.id}`} · {row.next_check}
              </Anchor>
              <Text size='sm'>
                {row.status.replaceAll('_', ' ')} · {t`Owner`}:{' '}
                {row.owner_name || (
                  <MissingValue
                    label={t`Owner`}
                    reason={t`No value recorded`}
                  />
                )}{' '}
                · {t`Next checkpoint`}:{' '}
                {row.next_checkpoint ? (
                  recordedTime(row.next_checkpoint)
                ) : (
                  <MissingValue
                    label={t`Next checkpoint`}
                    reason={t`No value recorded`}
                  />
                )}
              </Text>
              <Text size='xs'>
                {t`Source`}: {t`Request ${row.id}`} v{row.version} ·{' '}
                {t`Saved at`}: {recordedTime(row.created_at)} ·{' '}
                <MissingValue reason={t`Original trigger: unavailable`} />
              </Text>
              {row.blocked_reason && (
                <Text size='sm'>{row.blocked_reason}</Text>
              )}
            </Stack>
          ))
        )}
      </Stack>
      {params.get('work_panel') !== 'verify' && (
        <Anchor
          component={Link}
          replace
          to={`?${workPanelParams(params, 'verify')}`}
          size='sm'
        >{t`View test, release and operating records in Verify & Return`}</Anchor>
      )}
    </ExecutionSection>
  );
}
