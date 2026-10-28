import type { CloseoutWorkOrder } from '@lib/types/Closeout';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  Textarea,
  Title
} from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useRetainedCommand } from '../../../hooks/useRetainedCommand';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import { invalidateTestAlarmViews } from '../../assets/invalidateTestAlarmViews';
import {
  CloseoutReportDocument,
  ReportPDFButton
} from './CloseoutReportDocument';

interface ReportState {
  required: boolean;
  test_alarm: boolean;
  test_recovery?: {
    total: number;
    active: number;
    pending?: number;
    other_active_alarms: number;
  };
  status: string;
  report_id: number | null;
  report: {
    reference: string;
    title: string;
    account: Record<string, unknown>;
    capture_id: number | null;
  } | null;
  content_hash: string | null;
  submitted_by: number | null;
  submitted_by_name: string | null;
  approved_by: number | null;
  approved_by_name: string | null;
  verification_enabled: boolean;
  verified: boolean;
  sap_configured: boolean;
  version: number;
  effects: {
    effect_type: string;
    status: string;
    result_reference: string;
    last_error: string;
  }[];
}

export function ServiceReportWorkflow({
  workOrder,
  account,
  captureId,
  locked,
  ready,
  onChanged,
  onRestoreAccount
}: Readonly<{
  workOrder: CloseoutWorkOrder;
  account: Record<string, unknown>;
  captureId: number | null;
  locked: boolean;
  ready: boolean;
  onChanged: () => void;
  onRestoreAccount?: (account: Record<string, unknown>) => void;
}>) {
  const api = useApi();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const user = useUserState((state) => state.user);
  const generation = useUserState((state) => state.authGeneration);
  const host = useLocalState((state) => state.hostKey);
  const [opened, setOpened] = useState(false);
  const [preview, setPreview] = useState<'draft' | 'submitted' | null>(null);
  const [endTestOpen, setEndTestOpen] = useState(false);
  const [endTestReason, setEndTestReason] = useState('');
  const [endTestConfirmed, setEndTestConfirmed] = useState(false);
  const retained = useRetainedCommand(
    `service-report:${workOrder.id}`,
    new RegExp(
      `^/api/tasks/work-orders/${workOrder.id}/closeout/service-report/$`
    )
  );
  const { busy, intent } = retained;
  const query = useQuery({
    queryKey: [
      'service-report',
      host,
      user?.pk,
      generation,
      workOrder.id,
      workOrder.lifecycle_version
    ],
    enabled: opened,
    retry: false,
    queryFn: async () =>
      (
        await api.get<ReportState>(
          `/api/tasks/work-orders/${workOrder.id}/closeout/service-report/`
        )
      ).data
  });
  const state = !query.isError && !query.isFetching ? query.data : undefined;
  const unchangedSubmission =
    !!state?.report &&
    ['submitted', 'approved'].includes(state.status) &&
    state.report.capture_id === captureId &&
    Object.entries(state.report.account).every(([field, value]) => {
      const draft = account[field];
      const normal = (entry: unknown) =>
        field === 'downtime_minutes'
          ? (entry ?? null)
          : field === 'follow_up_required'
            ? (entry ?? false)
            : typeof entry === 'string'
              ? entry.trim()
              : (entry ?? '');
      return normal(draft) === normal(value);
    });
  const completed = workOrder.lifecycle_status === 'completed';
  const pendingTests =
    state?.test_recovery?.pending ?? state?.test_recovery?.active ?? 0;
  const canSubmit =
    !!user?.is_superuser || !!user?.permissions?.closeout?.includes('capture');
  const canApprove =
    !!user?.is_superuser || !!user?.permissions?.closeout?.includes('review');
  const canVerify =
    !!user?.is_superuser || !!user?.permissions?.closeout?.includes('verify');
  const blocked =
    locked ||
    busy ||
    retained.storageFailed ||
    !state ||
    state.version !== workOrder.lifecycle_version;
  const command = async (action: string) => {
    if (blocked || (intent && intent.body.action !== action)) return;
    const payload = intent?.body ?? {
      action,
      expected_version: workOrder.lifecycle_version,
      idempotency_key: crypto.randomUUID(),
      ...(action === 'end_test'
        ? { reason: endTestReason, confirmed: endTestConfirmed }
        : action === 'submit'
          ? { account, capture_id: captureId }
          : { report_id: state!.report_id })
    };
    const data = await retained.execute<{
      work_order_id: number;
      event_id: number;
      idempotency_key: string;
      command: string;
      metadata?: { equipment_url?: string };
    }>(
      {
        url: `/api/tasks/work-orders/${workOrder.id}/closeout/service-report/`,
        body: payload
      },
      (response, request) =>
        response.work_order_id === workOrder.id &&
        Number.isSafeInteger(response.event_id) &&
        response.event_id > 0 &&
        response.idempotency_key === request.body.idempotency_key &&
        response.command === `service_report_${request.body.action}`
    );
    if (data) {
      if (action === 'end_test') {
        setEndTestReason('');
        setEndTestConfirmed(false);
        setEndTestOpen(false);
      }
      onChanged();
      if (action === 'return' || action === 'end_test')
        await invalidateTestAlarmViews(queryClient);
      await query.refetch();
      if (
        (action === 'return' || action === 'end_test') &&
        typeof data.metadata?.equipment_url === 'string' &&
        /^\/machines\/machine\/\d+\/equipment\/(?:\?view=model&position=(?:motor|bearing|pump|discharge))?$/.test(
          data.metadata.equipment_url
        )
      ) {
        navigate(data.metadata.equipment_url);
      }
    }
  };
  const previewReport = preview === 'submitted' ? state?.report : null;
  return (
    <Card withBorder data-testid='service-report-workflow'>
      <Stack gap='sm'>
        <Group justify='space-between' wrap='wrap'>
          <Title order={3} size='h5'>{t`Service report and return`}</Title>
          <Button variant='light' onClick={() => setOpened(!opened)}>
            {opened ? t`Hide service report` : t`Open service report`}
          </Button>
        </Group>
        <Text
          size='sm'
          c='dimmed'
        >{t`Preview → submit → independent approval → native completion → verify and view equipment. Operational return authority remains separate.`}</Text>
        {opened && (
          <>
            {query.isFetching && <Loader size='sm' />}
            {query.isError && (
              <Alert color='red'>
                {t`Service report unavailable. No approval or recovery is confirmed.`}
                <Button
                  onClick={() => void query.refetch()}
                >{t`Reload report`}</Button>
              </Alert>
            )}
            {state && state.version !== workOrder.lifecycle_version && (
              <Alert color='orange'>
                {t`This Work changed in another session. Reload the Work and report before continuing.`}
                <Button
                  variant='subtle'
                  onClick={() => {
                    onChanged();
                    void query.refetch();
                  }}
                >{t`Reload report`}</Button>
              </Alert>
            )}
            {state && (
              <>
                <Group>
                  <Badge color={state.status === 'approved' ? 'blue' : 'gray'}>
                    {(
                      {
                        not_submitted: t`Not submitted`,
                        submitted: t`Awaiting approval`,
                        approved: t`Approved`,
                        stale: t`Changed — resubmit`
                      } as Record<string, string>
                    )[state.status] ?? t`Unknown status`}
                  </Badge>
                  {state.test_alarm && (
                    <Badge color='orange'>{t`Synthetic test`}</Badge>
                  )}
                </Group>
                <Text size='sm'>
                  {state.sap_configured
                    ? t`SAP connector configured. Writeback requires a provider acknowledgement.`
                    : t`SAP writeback: Not configured. No SAP update has been sent.`}
                </Text>
                {state.test_recovery && state.test_recovery.total > 0 && (
                  <>
                    <Badge
                      color={pendingTests === 0 ? 'green' : 'orange'}
                      tt='none'
                    >
                      {pendingTests === 0
                        ? t`Test exercise: normal`
                        : state.test_recovery.active === 0
                          ? t`Test exercise suppressed — not ended`
                          : t`Test alarm awaits verified closeout`}
                    </Badge>
                    {state.test_recovery.other_active_alarms > 0 && (
                      <Alert color='orange'>{t`Other real equipment alarms remain active. Finishing this test will not force their readings or status to green.`}</Alert>
                    )}
                  </>
                )}
                {user?.is_superuser && pendingTests > 0 && (
                  <>
                    <Button
                      variant='outline'
                      color='orange'
                      disabled={blocked || !!intent}
                      onClick={() => setEndTestOpen(true)}
                    >{t`End test exercise`}</Button>
                    <Text
                      size='xs'
                      c='dimmed'
                    >{t`Stops only the synthetic exercise. It does not complete the repair, approve this Work, or clear real equipment alarms.`}</Text>
                    <Modal
                      opened={endTestOpen}
                      onClose={() => {
                        if (!busy) setEndTestOpen(false);
                      }}
                      title={t`End test exercise`}
                      closeOnEscape={!busy}
                      closeOnClickOutside={!busy}
                      withCloseButton={!busy}
                    >
                      <Stack>
                        <Alert color='orange'>{t`The test alarm will return to normal, but actual measurements and real alarms will retain their current state. Work will not be marked completed.`}</Alert>
                        {['submitted', 'approved'].includes(state.status) && (
                          <Alert color='orange'>{t`Ending this exercise changes the reviewed alarm evidence. Submit and independently approve an updated report before completing Work.`}</Alert>
                        )}
                        <Textarea
                          label={t`Reason for ending the test`}
                          description={t`Enter at least 5 characters.`}
                          value={endTestReason}
                          onChange={(event) =>
                            setEndTestReason(event.currentTarget.value)
                          }
                          disabled={busy}
                          required
                        />
                        <Checkbox
                          label={t`I confirm this ends only a synthetic exercise, not a repair`}
                          checked={endTestConfirmed}
                          onChange={(event) =>
                            setEndTestConfirmed(event.currentTarget.checked)
                          }
                          disabled={busy}
                        />
                        {retained.error && (
                          <Alert color='red'>{retained.error}</Alert>
                        )}
                        <Button
                          color='orange'
                          disabled={
                            blocked ||
                            !endTestConfirmed ||
                            endTestReason.trim().length < 5
                          }
                          loading={busy}
                          onClick={() => void command('end_test')}
                        >{t`End test and view equipment`}</Button>
                      </Stack>
                    </Modal>
                  </>
                )}
                {!completed && workOrder.lifecycle_status !== 'verifying' && (
                  <Alert color='blue'>{t`Use the preparation steps above to finish execution and enter verification before submitting this report.`}</Alert>
                )}
                {!completed && !ready && state.status !== 'approved' && (
                  <Text
                    size='sm'
                    c='orange'
                  >{t`Enter Action taken, Result and Verification summary below, or finish the saved capture review. Submission is unavailable until this account is complete.`}</Text>
                )}
                {state.submitted_by_name && (
                  <Text size='sm'>
                    {t`Submitted by`}: {state.submitted_by_name}
                  </Text>
                )}
                {state.approved_by_name && (
                  <Text size='sm'>
                    {t`Approved by`}: {state.approved_by_name}
                  </Text>
                )}
                {!completed && state.status === 'approved' && (
                  <Alert color='blue'>{t`Approved. Complete the Work below to enable verification.`}</Alert>
                )}
                {!completed && state.report && !unchangedSubmission && (
                  <Text
                    size='sm'
                    c='orange'
                  >{t`Resubmitting a changed report requires a new independent approval.`}</Text>
                )}
                {completed &&
                  !state.verification_enabled &&
                  !state.verified && (
                    <Alert color='orange'>{t`Supervisor verification is disabled by deployment policy. Test recovery and return are not available.`}</Alert>
                  )}
                {state.effects.map((effect) => (
                  <Text key={effect.effect_type} size='sm'>
                    {effect.effect_type === 'sap_writeback'
                      ? t`SAP writeback`
                      : t`Recovery notification`}
                    : {effect.status}
                    {effect.result_reference &&
                      ` · ${
                        effect.effect_type === 'test_recovery' &&
                        /^notification requested:\d+$/.test(
                          effect.result_reference
                        )
                          ? t`Notification requested for ${Number(effect.result_reference.split(':')[1])} recipient(s)`
                          : effect.result_reference
                      }`}
                    {effect.last_error && ` · ${effect.last_error}`}
                  </Text>
                ))}
                {retained.error && (
                  <Alert color='red' title={t`Command outcome not confirmed`}>
                    {retained.error}
                  </Alert>
                )}
                <Group wrap='wrap'>
                  <Button
                    variant='default'
                    onClick={() =>
                      setPreview(state.report ? 'submitted' : 'draft')
                    }
                  >
                    {state.report
                      ? t`Preview submitted report`
                      : t`Preview draft report`}
                  </Button>
                  {state.report && !completed && (
                    <Button
                      variant='default'
                      onClick={() => setPreview('draft')}
                    >{t`Preview current draft`}</Button>
                  )}
                  {!completed && canSubmit && (
                    <Button
                      variant={state.report ? 'default' : 'filled'}
                      disabled={
                        blocked ||
                        !ready ||
                        !!intent ||
                        unchangedSubmission ||
                        workOrder.lifecycle_status !== 'verifying'
                      }
                      onClick={() => void command('submit')}
                    >
                      {state.report
                        ? t`Resubmit service report`
                        : t`Submit service report`}
                    </Button>
                  )}
                  {!completed && canApprove && (
                    <Button
                      disabled={
                        blocked ||
                        !!intent ||
                        state.status !== 'submitted' ||
                        state.submitted_by === user?.pk
                      }
                      onClick={() => void command('approve')}
                    >{t`Approve service report`}</Button>
                  )}
                  {!completed && state.report && onRestoreAccount && (
                    <Button
                      variant='default'
                      disabled={blocked || !!intent}
                      onClick={() => onRestoreAccount(state.report!.account)}
                    >{t`Use submitted account`}</Button>
                  )}
                  {completed && canVerify && (
                    <>
                      <Button
                        variant='default'
                        disabled={
                          blocked ||
                          !!intent ||
                          !state.sap_configured ||
                          state.effects.some(
                            (effect) => effect.effect_type === 'sap_writeback'
                          ) ||
                          state.status !== 'approved'
                        }
                        onClick={() => void command('sap')}
                      >{t`Write back to SAP`}</Button>
                      <Button
                        disabled={
                          blocked ||
                          !!intent ||
                          state.status !== 'approved' ||
                          (!state.verified && !state.verification_enabled)
                        }
                        onClick={() => void command('return')}
                      >{t`Verify and view equipment`}</Button>
                    </>
                  )}
                  {intent && (
                    <Button
                      disabled={blocked}
                      loading={busy}
                      onClick={() => void command(String(intent.body.action))}
                    >{t`Retry same command`}</Button>
                  )}
                  <Button
                    variant='subtle'
                    disabled={busy}
                    onClick={() => void query.refetch()}
                  >{t`Reload report`}</Button>
                </Group>
                {state.status === 'submitted' && (
                  <Text size='sm'>{t`A different reviewer must approve the exact submitted report. Changed account, material or reading evidence requires resubmission.`}</Text>
                )}
                <Modal
                  opened={preview !== null}
                  onClose={() => setPreview(null)}
                  title={
                    previewReport
                      ? t`Submitted service report`
                      : t`Draft service report`
                  }
                  size='xl'
                  closeButtonProps={{ 'aria-label': t`Close report preview` }}
                >
                  <Stack>
                    <Text size='sm'>
                      {previewReport
                        ? t`Retained server snapshot; not an operating return authorization.`
                        : t`Unsaved draft — not submitted or approved.`}
                    </Text>
                    <ReportPDFButton
                      workId={workOrder.id}
                      version={workOrder.lifecycle_version}
                      source={previewReport ? 'submitted' : 'draft'}
                      reportId={state.report_id}
                      account={account}
                      disabled={query.isFetching || query.isError}
                    />
                    <CloseoutReportDocument
                      reference={
                        previewReport?.reference ?? workOrder.reference
                      }
                      title={previewReport?.title ?? workOrder.title}
                      account={previewReport?.account ?? account}
                      status={
                        previewReport ? t`Submitted snapshot` : t`Unsaved draft`
                      }
                      contentHash={previewReport ? state.content_hash : null}
                    />
                  </Stack>
                </Modal>
              </>
            )}
          </>
        )}
      </Stack>
    </Card>
  );
}
