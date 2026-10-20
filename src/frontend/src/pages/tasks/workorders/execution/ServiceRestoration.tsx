import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { Anchor, Badge, Group, SimpleGrid, Stack, Text } from '@mantine/core';
import { Link, useSearchParams } from 'react-router-dom';
import { workPanelParams } from '../workPanelState';
import {
  ExecutionFact,
  ExecutionSection,
  recordedTime
} from './ExecutionFacts';
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
    >
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <ExecutionFact label={t`Native Work lifecycle`}>
          {workOrder.lifecycle_status?.replaceAll('_', ' ') || t`Unavailable`}
        </ExecutionFact>
        <ExecutionFact label={t`Native completion time`}>
          {recordedTime(workOrder.actual_completed_at)}
        </ExecutionFact>
        <ExecutionFact label={t`Maintenance record`}>
          {closeout
            ? t`Updated · Closeout ${closeout.id}`
            : t`No accepted closeout supplied`}
        </ExecutionFact>
        <ExecutionFact label={t`Saved physical test outcomes`}>
          {tests
            ? t`Recorded passes: ${tests.filter((row) => testEvaluation(row) === 'recorded_pass').length} · Current failures: ${tests.filter((row) => testEvaluation(row) === 'failed').length}`
            : t`Unavailable — test records not supplied`}
        </ExecutionFact>
      </SimpleGrid>
      {closeout && (
        <Text size='xs' c='dimmed'>
          {t`Accepted account source`}: {closeout.id} ·{' '}
          {closeout.completed_by_name || t`Recorder unavailable`} ·{' '}
          {recordedTime(closeout.completed_at)}
        </Text>
      )}
      <Group gap='xs'>
        {[
          t`Condition controlled: unknown`,
          t`Asset repair: unconfirmed`,
          t`Test acceptance: unavailable`,
          t`Operating return: awaiting record`,
          t`Service: awaiting verification`,
          t`Resilience: unknown`
        ].map((label) => (
          <Badge
            key={label}
            color='gray'
            variant='light'
            styles={{
              root: { height: 'auto' },
              label: { whiteSpace: 'normal' }
            }}
          >
            {label}
          </Badge>
        ))}
      </Group>
      <Text size='sm'>{t`Awaiting service verification — duty, affected process, standby capacity, operating conditions and operations-owned restrictions are unavailable from the current providers. Their source, responsible owner and effective time are unknown.`}</Text>
      <Text
        size='sm'
        c='dimmed'
      >{t`Work completion, a recorded pass or equipment running does not establish accepted tests, operating permission, full service, restored standby capacity or closed obligations.`}</Text>
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
              <Text size='xs' c='dimmed'>
                {t`Source`}: {t`Closeout`} {closeout.id} · {t`Saved at`}:{' '}
                {recordedTime(closeout.completed_at)}
              </Text>
              <Text size='xs'>{t`Follow-up owner, original trigger, checkpoint and resolution are not supplied. The maintenance update does not resolve this follow-up.`}</Text>
            </Stack>
          )}
        {unresolved === undefined ? (
          <Text size='sm'>{t`Saved request queue unavailable; remaining requests are unknown.`}</Text>
        ) : unresolved.length === 0 ? (
          <Text size='sm'>{t`No unresolved investigation requests returned. This does not establish that all obligations are closed.`}</Text>
        ) : (
          unresolved.map((row) => (
            <Stack key={row.id} gap={2}>
              <Anchor component={Link} to={requestLink(row.id)} size='sm'>
                {t`Request ${row.id}`} · {row.next_check}
              </Anchor>
              <Text size='sm'>
                {row.status.replaceAll('_', ' ')} · {t`Owner`}:{' '}
                {row.owner_name || t`Not recorded`} · {t`Next checkpoint`}:{' '}
                {recordedTime(row.next_checkpoint)}
              </Text>
              <Text size='xs' c='dimmed'>
                {t`Source`}: {t`Request ${row.id}`} v{row.version} ·{' '}
                {t`Saved at`}: {recordedTime(row.created_at)} ·{' '}
                {t`Original trigger: unavailable`}
              </Text>
              {row.blocked_reason && (
                <Text size='sm'>{row.blocked_reason}</Text>
              )}
            </Stack>
          ))
        )}
        <Text
          size='xs'
          c='dimmed'
        >{t`Service, financial, reporting, quality, permanent-repair and residual obligations are not supplied as an authoritative register. Applicability, accepted owners and deadlines cannot be inferred from this account or request queue.`}</Text>
      </Stack>
      {params.get('work_panel') !== 'verify' && (
        <Anchor
          component={Link}
          to={`?${workPanelParams(params, 'verify')}`}
          size='sm'
        >{t`View test, release and operating records in Verify & Return`}</Anchor>
      )}
    </ExecutionSection>
  );
}
