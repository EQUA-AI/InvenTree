import { displayUpdate } from '@lib/types/CanonicalDisplay';
import type { CloseoutAmendment } from '@lib/types/Closeout';
import type {
  StructuredCloseoutOverview,
  WorkOrderOverview
} from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Divider,
  Group,
  Loader,
  NumberInput,
  Select,
  Stack,
  Stepper,
  Table,
  Text,
  TextInput,
  Textarea,
  Title
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconListCheck,
  IconRefresh,
  IconSparkles
} from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { InfoTooltip } from '../../../components/items/ValueTooltip';
import { WorkTableScroll } from '../../../components/tables/WorkTableScroll';
import { CloseoutReconciliation } from './CloseoutReconciliation';
import DictateCloseoutModal from './DictateCloseoutModal';
import { DraftRecovery } from './DraftRecovery';
import { WorkRecordUpdate } from './FinalUpdateAction';
import { PreparedCloseout } from './PreparedCloseout';
import {
  fieldDecisions,
  reviewedAccountValues,
  savedFieldValue
} from './closeoutAccount';
import { WarningText } from './execution/ExecutionFacts';
import { ServiceRestoration } from './execution/ServiceRestoration';
import { readingStatusLabel } from './execution/routinePresentation';
import { narrativeExcerpts } from './storyProvenance';
import { useActionFocus } from './useActionFocus';
import { useCaptureSave } from './useCaptureSave';
import { useWorkRecordUpdate } from './useWorkRecordUpdate';
import type { UpdateIntent } from './workRecordUpdate';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type {
  CloseoutCapture,
  CloseoutProposal,
  CloseoutWorkOrder,
  CloseoutEffectRow as EffectRow,
  CloseoutPartUsageRow as PartUsageRow,
  CloseoutReadiness as Readiness,
  CloseoutReadingRow as ReadingRow
} from '@lib/types/Closeout';

import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';

export type { CloseoutWorkOrder } from '@lib/types/Closeout';

const ACTIVE_CAPTURE_STATES = ['open', 'extracting', 'proposed', 'reviewed'];
const REQUIRED_FIELDS = ['action', 'result', 'verification_summary'] as const;

const FIELD_LABELS: Record<string, string> = {
  cause: 'Cause',
  action: 'Action taken',
  result: 'Result',
  verification_summary: 'Verification summary'
};

function newIdempotencyKey(): string {
  return `co-${crypto.randomUUID()}`;
}

/**
 * Closeout account with native capture recovery (Feature #15, CO1 surface).
 *
 * Server readiness remains authoritative. The prepared report and one update
 * action are direct; the native stepper retains capture/reconciliation recovery.
 */
export function CloseoutPanel({
  workOrder,
  accepted,
  execution,
  packetId,
  captureEnabled = true
}: Readonly<{
  workOrder: CloseoutWorkOrder;
  accepted?: StructuredCloseoutOverview | null;
  execution?: WorkOrderOverview;
  packetId?: number;
  captureEnabled?: boolean;
}>) {
  const api = useApi();
  const user = useUserState((state) => state.user);
  const canCapture =
    !!user?.is_superuser || !!user?.permissions?.closeout?.includes('capture');
  const canReview =
    !!user?.is_superuser || !!user?.permissions?.closeout?.includes('review');
  const canReconcile =
    !!user?.is_superuser ||
    !!user?.permissions?.closeout_parts?.includes('reconcile');
  const canComplete =
    !!user?.is_superuser ||
    !!user?.permissions?.workorder?.includes('complete');
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);
  const [captureEditing, setCaptureEditing] = useState(false);
  const [narrative, setNarrative] = useState('');
  const [voiceReason, setVoiceReason] = useState('');
  const [dictateOpen, setDictateOpen] = useState(false);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [downtime, setDowntime] = useState<number | ''>('');
  const [followUp, setFollowUp] = useState('');
  const [followUpRequired, setFollowUpRequired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reviewedVersion, setReviewedVersion] = useState<number | null>(null);
  const [draftBase, setDraftBase] = useState<{
    version: number;
    decision?: string;
    captureId: number | null;
  } | null>(null);
  const [latestReview, setLatestReview] = useState<{
    version: number;
    decision?: string;
    captureId: number | null;
    fields: Record<string, string>;
  } | null>(null);
  const [reviewError, setReviewError] = useState(false);
  const [commandFailure, setCommandFailure] = useState<string | null>(null);
  const [partDrafts, setPartDrafts] = useState<
    Record<number, { disposition: string; reason: string; version: number }>
  >({});
  const [partFailure, setPartFailure] = useState<PartUsageRow | null>(null);
  const [partLatest, setPartLatest] = useState<PartUsageRow | null>(null);

  const woId = workOrder.id;

  const capturesQuery = useQuery({
    queryKey: ['closeout-captures', woId],
    enabled: captureEnabled,
    queryFn: async () => {
      const response = await api.get<CloseoutCapture[]>(
        apiUrl(ApiEndpoints.work_order_closeout_captures, woId)
      );
      return response.data;
    }
  });

  const capture = useMemo(
    () =>
      (captureEnabled ? (capturesQuery.data ?? []) : []).find((row) =>
        ACTIVE_CAPTURE_STATES.includes(row.status)
      ) ?? null,
    [captureEnabled, capturesQuery.data]
  );

  const accountCapture = !captureEnabled
    ? null
    : accepted
      ? (capturesQuery.data?.find(
          (row) => row.completed_closeout === accepted.id
        ) ?? null)
      : capture;
  const captureId = accountCapture?.id ?? null;
  const proposalQuery = useQuery({
    queryKey: ['closeout-proposal', woId, captureId],
    enabled:
      captureEnabled &&
      captureId != null &&
      ['proposed', 'reviewed', 'consumed'].includes(
        accountCapture?.status ?? ''
      ),
    retry: false,
    queryFn: async () => {
      const response = await api.get<CloseoutProposal>(
        apiUrl(ApiEndpoints.work_order_closeout_capture_proposal, woId, {
          capId: captureId ?? 0
        })
      );
      return response.data;
    }
  });

  const proposal = captureEnabled ? proposalQuery.data : undefined;

  const partUsageQuery = useQuery({
    queryKey: ['closeout-part-usage', woId],
    enabled: captureEnabled,
    queryFn: async () => {
      const response = await api.get<PartUsageRow[]>(
        apiUrl(ApiEndpoints.work_order_closeout_part_usage, woId)
      );
      return response.data;
    }
  });

  const readingsQuery = useQuery({
    queryKey: ['closeout-readings', woId],
    enabled: captureEnabled,
    queryFn: async () => {
      const response = await api.get<ReadingRow[]>(
        apiUrl(ApiEndpoints.work_order_closeout_readings, woId)
      );
      return response.data;
    }
  });

  const readinessQuery = useQuery({
    queryKey: [
      'work-order-readiness',
      woId,
      'complete',
      ...(packetId != null ? [packetId] : [])
    ],
    queryFn: async () => {
      const response = await api.get<
        Readiness & { work_order_id?: number; packet_id?: number }
      >(
        packetId != null
          ? apiUrl(ApiEndpoints.repair_packet_close, packetId)
          : `${apiUrl(ApiEndpoints.work_order_readiness, woId)}?action=complete`
      );
      if (
        packetId != null &&
        (response.data.work_order_id !== woId ||
          response.data.packet_id !== packetId)
      )
        throw new Error('Packet readiness does not match this Work');
      return response.data;
    }
  });

  const amendmentsQuery = useQuery({
    queryKey: ['closeout-amendments', woId],
    enabled: captureEnabled && accepted != null,
    retry: false,
    queryFn: async () =>
      (
        await api.get<CloseoutAmendment[]>(
          apiUrl(ApiEndpoints.work_order_closeout_amendments, woId)
        )
      ).data
  });

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['closeout-captures', woId] });
    queryClient.invalidateQueries({ queryKey: ['closeout-proposal', woId] });
    queryClient.invalidateQueries({ queryKey: ['closeout-part-usage', woId] });
    queryClient.invalidateQueries({ queryKey: ['closeout-readings', woId] });
    queryClient.invalidateQueries({
      queryKey: ['work-order-readiness', woId, 'complete']
    });
    queryClient.invalidateQueries({ queryKey: ['work-order', woId] });
  }, [queryClient, woId]);

  const update = useWorkRecordUpdate(woId, invalidate, packetId);
  const captureSave = useCaptureSave(woId, () => {
    setDraftBase(null);
    setFieldValues({});
    setNarrative('');
    setVoiceReason('');
    setLatestReview(null);
    invalidate();
  });
  useEffect(() => {
    const intent = captureSave.state?.intent;
    if (!intent) return;
    if (intent.command === 'closeout_capture') {
      setNarrative(String(intent.payload.narrative ?? ''));
      setVoiceReason(String(intent.payload.voice_unavailable_reason ?? ''));
    } else setFieldValues(intent.draft);
    setDraftBase({
      version: intent.payload.expected_version,
      decision:
        typeof intent.payload.expected_decision_version === 'string'
          ? intent.payload.expected_decision_version
          : undefined,
      captureId: Number(intent.url.match(/captures\/(\d+)\//)?.[1]) || null
    });
  }, [captureSave.state?.intent]);
  const wizardBlocked =
    captureEnabled &&
    (capturesQuery.isPending ||
      proposalQuery.isFetching ||
      partUsageQuery.isPending ||
      readingsQuery.isPending ||
      capturesQuery.isError ||
      proposalQuery.isError ||
      partUsageQuery.isError ||
      readingsQuery.isError);
  const mutationLocked =
    busy ||
    wizardBlocked ||
    captureSave.state != null ||
    dictateOpen ||
    ['pending', 'uncertain', 'confirmed'].includes(update.state.status);
  // Retained commands still require today's authority and readable sources.
  // Their own recovery state must not prevent an authorized exact retry.
  const captureRetryLocked =
    !(captureSave.state?.intent.command === 'closeout_decisions'
      ? canReview
      : canCapture) ||
    wizardBlocked ||
    busy ||
    dictateOpen ||
    ['pending', 'uncertain', 'confirmed'].includes(update.state.status);
  const finalUpdateLocked =
    !canComplete ||
    wizardBlocked ||
    busy ||
    dictateOpen ||
    captureSave.state != null;
  const effectsQuery = useQuery({
    queryKey: ['closeout-effects', woId],
    enabled:
      captureEnabled &&
      (update.state.status === 'confirmed' || accepted != null),
    queryFn: async () => {
      const response = await api.get<EffectRow[]>(
        apiUrl(ApiEndpoints.work_order_closeout_effects, woId)
      );
      return response.data;
    }
  });

  const runCommand = useCallback(
    async (fn: () => Promise<void>) => {
      if (mutationLocked) return;
      setBusy(true);
      setCommandFailure(null);
      try {
        await fn();
        invalidate();
      } catch (error) {
        setCommandFailure(
          t`Command not confirmed. Your inputs are retained. Review the current record before retrying.`
        );
      } finally {
        setBusy(false);
      }
    },
    [invalidate, mutationLocked]
  );

  const decisionToken = proposal
    ? proposal.decision_version
    : capture?.decision_version;
  const base = () =>
    draftBase ?? {
      version: workOrder.lifecycle_version,
      decision: decisionToken,
      captureId
    };
  const createCapture = () => {
    if (
      !canCapture ||
      mutationLocked ||
      capture ||
      !narrative.trim() ||
      !voiceReason.trim()
    )
      return;
    return captureSave.save({
      url: apiUrl(ApiEndpoints.work_order_closeout_captures, woId),
      command: 'closeout_capture',
      payload: {
        expected_version: base().version,
        idempotency_key: newIdempotencyKey(),
        narrative,
        voice_unavailable_reason: voiceReason
      },
      draft: {
        [t`Narrative`]: narrative,
        [t`Why voice is unavailable or unusable`]: voiceReason
      }
    });
  };

  const extract = () =>
    runCommand(async () => {
      if (!canCapture || !capture) return;
      await api.post(
        apiUrl(ApiEndpoints.work_order_closeout_capture_extract, woId, {
          capId: capture.id
        }),
        {}
      );
      notifications.show({
        message: t`Extraction proposal ready for review`,
        color: 'green'
      });
    });

  const effectiveFieldValue = useCallback(
    (field: string): string =>
      fieldValues[field] ?? savedFieldValue(proposal, field),
    [fieldValues, proposal]
  );

  const submitDecisions = () => {
    if (!canReview || mutationLocked || !capture || !base().decision) return;
    const fields = Object.fromEntries(
      ['cause', ...REQUIRED_FIELDS].map((field) => [
        field,
        effectiveFieldValue(field)
      ])
    );
    return captureSave.save({
      url: apiUrl(ApiEndpoints.work_order_closeout_capture_decisions, woId, {
        capId: base().captureId ?? capture.id
      }),
      command: 'closeout_decisions',
      payload: {
        expected_version: base().version,
        expected_decision_version: base().decision,
        idempotency_key: newIdempotencyKey(),
        decisions: fieldDecisions(proposal, fields)
      },
      draft: fields
    });
  };
  const reviewCapture = async () => {
    setLatestReview(null);
    setReviewError(false);
    try {
      const overview = (
        await api.get<{ lifecycle_version: number }>(
          apiUrl(ApiEndpoints.kanban_card_overview, woId)
        )
      ).data;
      const captures = (
        await api.get<CloseoutCapture[]>(
          apiUrl(ApiEndpoints.work_order_closeout_captures, woId)
        )
      ).data;
      const latestCapture = captures.find((row) =>
        ACTIVE_CAPTURE_STATES.includes(row.status)
      );
      const proposal =
        latestCapture && ['reviewed', 'proposed'].includes(latestCapture.status)
          ? (
              await api.get<CloseoutProposal>(
                apiUrl(
                  ApiEndpoints.work_order_closeout_capture_proposal,
                  woId,
                  { capId: latestCapture.id }
                )
              )
            ).data
          : null;
      setLatestReview({
        version: overview.lifecycle_version,
        decision: proposal
          ? proposal.decision_version
          : latestCapture?.decision_version,
        captureId: latestCapture?.id ?? null,
        fields:
          captureSave.state?.intent.command === 'closeout_capture'
            ? {
                [t`Narrative`]: latestCapture?.narrative ?? t`Not recorded`,
                [t`Why voice is unavailable or unusable`]:
                  latestCapture?.voice_unavailable_reason || t`Not recorded`
              }
            : Object.fromEntries(
                ['cause', ...REQUIRED_FIELDS].map((field) => [
                  field,
                  savedFieldValue(proposal, field)
                ])
              )
      });
    } catch {
      setReviewError(true);
    }
  };
  const useReviewedCapture = (keep: boolean) => {
    if (!latestReview || !captureSave.state) return;
    if (
      keep &&
      captureSave.state.intent.command === 'closeout_decisions' &&
      latestReview.captureId !== base().captureId
    )
      return;
    if (
      captureSave.state.intent.command === 'closeout_capture' &&
      latestReview.captureId != null
    )
      return;
    if (captureSave.state.intent.command === 'closeout_decisions')
      setFieldValues(
        keep ? captureSave.state.intent.draft : latestReview.fields
      );
    else if (keep) {
      setNarrative(String(captureSave.state.intent.payload.narrative ?? ''));
      setVoiceReason(
        String(captureSave.state.intent.payload.voice_unavailable_reason ?? '')
      );
    }
    setDraftBase({
      version: latestReview.version,
      decision: latestReview.decision,
      captureId: latestReview.captureId
    });
    captureSave.release();
    setLatestReview(null);
    invalidate();
  };

  const refreshPartUsage = () =>
    runCommand(async () => {
      if (!canCapture) return;
      await api.post(
        apiUrl(ApiEndpoints.work_order_closeout_part_usage_refresh, woId),
        {}
      );
    });

  const resolveRow = async (
    row: PartUsageRow,
    disposition: string,
    reason: string
  ) => {
    if (!canReconcile || mutationLocked || partFailure) return;
    setBusy(true);
    try {
      await api.post(
        apiUrl(ApiEndpoints.work_order_closeout_part_usage_resolve, woId, {
          rowId: row.id
        }),
        {
          disposition,
          reason,
          expected_row_version: partDrafts[row.id]?.version ?? row.version
        }
      );
      setPartDrafts((current) => {
        const next = { ...current };
        delete next[row.id];
        return next;
      });
      invalidate();
    } catch {
      setPartFailure(row);
      setPartLatest(null);
    } finally {
      setBusy(false);
    }
  };
  const reviewPart = async () => {
    setPartLatest(null);
    setReviewError(false);
    try {
      const rows = (
        await api.get<PartUsageRow[]>(
          apiUrl(ApiEndpoints.work_order_closeout_part_usage, woId)
        )
      ).data;
      const latest = rows.find((row) => row.id === partFailure?.id);
      if (!latest) throw new Error('Unavailable row');
      setPartLatest(latest);
    } catch {
      setReviewError(true);
    }
  };

  const accountReady =
    REQUIRED_FIELDS.every(
      (field) => effectiveFieldValue(field).trim().length > 0
    ) &&
    (!capture ||
      (capture.status === 'reviewed' &&
        REQUIRED_FIELDS.every(
          (field) =>
            effectiveFieldValue(field) === savedFieldValue(proposal, field)
        ))) &&
    !wizardBlocked;
  const complete = () => {
    if (
      !canComplete ||
      !accountReady ||
      mutationLocked ||
      readinessQuery.isError ||
      readinessQuery.isFetching ||
      !readinessQuery.data?.ready ||
      workOrder.lifecycle_status === 'completed'
    )
      return;
    const payload: UpdateIntent['payload'] = {
      expected_version: base().version,
      idempotency_key: newIdempotencyKey(),
      action: effectiveFieldValue('action'),
      result: effectiveFieldValue('result'),
      verification_summary: effectiveFieldValue('verification_summary'),
      cause: effectiveFieldValue('cause'),
      follow_up: followUpRequired ? followUp : '',
      follow_up_required: followUpRequired,
      ...(downtime !== '' ? { downtime_minutes: downtime } : {}),
      ...(capture?.status === 'reviewed' ? { capture_id: capture.id } : {})
    };
    return update.execute({
      workOrderId: woId,
      ...(packetId != null ? { packetId } : {}),
      payload
    });
  };

  const readiness = readinessQuery.isError ? undefined : readinessQuery.data;
  const changedFields = Object.entries(fieldValues).some(
    ([key, value]) => value !== savedFieldValue(proposal, key)
  );
  const captureInProgress = capture != null && capture.status !== 'reviewed';
  const hasCaptureDraft =
    !!narrative.trim() || !!voiceReason.trim() || changedFields;
  useEffect(() => {
    // A ready, saved account is direct; unfinished capture work is visible.
    // Once opened, native panel retention also preserves this disclosure state.
    if (captureInProgress || dictateOpen || hasCaptureDraft)
      setCaptureEditing(true);
  }, [captureInProgress, dictateOpen, hasCaptureDraft]);

  return (
    <Stack gap='md'>
      <Title order={2} size='h4'>{t`Closeout`}</Title>
      {captureEnabled && (
        <DictateCloseoutModal
          opened={dictateOpen}
          locked={!canCapture || wizardBlocked}
          onClose={() => setDictateOpen(false)}
          workOrderId={workOrder.id}
          workOrderVersion={workOrder.lifecycle_version}
          onCommitted={() => void invalidate()}
        />
      )}
      {captureEnabled &&
        !accepted &&
        !capturesQuery.isPending &&
        !capturesQuery.isError &&
        !proposalQuery.isFetching &&
        !proposalQuery.isError &&
        REQUIRED_FIELDS.some((field) => !effectiveFieldValue(field).trim()) && (
          <Card withBorder mb='md' data-testid='closeout-questions'>
            <Stack gap='xs'>
              <Title order={3} size='h5'>{t`Complete account`}</Title>
              {!capture && capturesQuery.data?.length === 0 && (
                <Group>
                  <Button
                    color='grape'
                    disabled={
                      !canCapture || mutationLocked || !!narrative.trim()
                    }
                    onClick={() => setDictateOpen(true)}
                  >{t`Start voice-transcript capture`}</Button>
                  <Button
                    variant='default'
                    disabled={!canCapture || mutationLocked}
                    onClick={() => {
                      setCaptureEditing(true);
                      setStep(0);
                    }}
                  >{t`Use typed story fallback`}</Button>
                </Group>
              )}
              {REQUIRED_FIELDS.filter(
                (field) => !effectiveFieldValue(field).trim()
              ).map((field) => (
                <Button
                  key={field}
                  variant='subtle'
                  styles={{
                    label: { whiteSpace: 'normal' },
                    root: { height: 'auto' }
                  }}
                  onClick={() => {
                    setCaptureEditing(true);
                    setStep(1);
                  }}
                >
                  {field === 'action'
                    ? t`What work was actually done, and why?`
                    : field === 'result'
                      ? t`What was the observed result?`
                      : t`What verification was recorded?`}
                </Button>
              ))}
              <InfoTooltip
                label={
                  capturesQuery.data?.length
                    ? t`Saved story and answers are reused. Coverage of finding, failed approaches, practical advice and residual uncertainty has no structured provider; unasked topics are not marked complete.`
                    : t`Add the finding and supporting evidence, what changed the diagnosis, useful practical advice and remaining uncertainty to the existing story capture. Cause may remain not established.`
                }
              />
            </Stack>
          </Card>
        )}
      {(capturesQuery.isError ||
        proposalQuery.isError ||
        partUsageQuery.isError) && (
        <Alert color='orange'>
          {t`Closeout source unavailable. Last-known values and drafts are retained; saving is locked.`}
          <Button
            variant='light'
            onClick={invalidate}
          >{t`Retry sources`}</Button>
        </Alert>
      )}
      {commandFailure && <Alert color='orange'>{commandFailure}</Alert>}
      {partFailure && (
        <DraftRecovery
          title={t`Material resolution not confirmed`}
          message={
            reviewError
              ? t`Latest row unavailable. Your draft is retained.`
              : t`Compare the saved row before another resolution. Your disposition and reason are retained.`
          }
          draft={{
            [t`Disposition`]: partDrafts[partFailure.id]?.disposition ?? '',
            [t`Reason`]: partDrafts[partFailure.id]?.reason ?? '',
            [t`Row version`]: String(
              partDrafts[partFailure.id]?.version ?? partFailure.version
            )
          }}
          latest={
            partLatest
              ? {
                  [t`Disposition`]: partLatest.disposition,
                  [t`Reason`]: partLatest.variance_reason ?? t`Not recorded`,
                  [t`State`]: partLatest.state,
                  [t`Row version`]: String(partLatest.version)
                }
              : undefined
          }
          onReview={() => void reviewPart()}
          onKeep={
            partLatest && partLatest.state !== 'reconciled'
              ? () => {
                  setPartDrafts((current) => ({
                    ...current,
                    [partLatest.id]: {
                      ...current[partLatest.id],
                      version: partLatest.version
                    }
                  }));
                  setPartFailure(null);
                  invalidate();
                }
              : undefined
          }
          onLatest={
            partLatest
              ? () => {
                  setPartDrafts((current) => {
                    const next = { ...current };
                    delete next[partLatest.id];
                    return next;
                  });
                  setPartFailure(null);
                  invalidate();
                }
              : undefined
          }
        />
      )}
      {capture && !decisionToken && (
        <Alert color='orange'>{t`Capture review cannot be saved safely on this server. Your draft is retained; the current saved revision is unavailable.`}</Alert>
      )}
      {captureSave.state && captureSave.state.status !== 'pending' && (
        <DraftRecovery
          title={t`Capture save needs attention`}
          message={
            reviewError
              ? t`Latest record unavailable. Your draft is retained; access or source could not be confirmed.`
              : captureSave.message
          }
          draft={captureSave.state.intent.draft}
          latest={
            latestReview
              ? {
                  [t`Work version`]: String(latestReview.version),
                  [t`Capture`]:
                    latestReview.captureId == null
                      ? t`Not recorded`
                      : String(latestReview.captureId),
                  ...latestReview.fields
                }
              : undefined
          }
          onReview={
            ['stale', 'failed'].includes(captureSave.state.status)
              ? () => void reviewCapture()
              : undefined
          }
          onRetry={
            ['uncertain', 'failed'].includes(captureSave.state.status)
              ? () => {
                  if (!captureRetryLocked) void captureSave.save();
                }
              : undefined
          }
          retryDisabled={captureRetryLocked}
          onKeep={
            latestReview &&
            !(
              captureSave.state.intent.command === 'closeout_capture' &&
              latestReview.captureId != null
            ) &&
            (captureSave.state.intent.command !== 'closeout_decisions' ||
              latestReview.captureId === base().captureId)
              ? () => useReviewedCapture(true)
              : undefined
          }
          onLatest={
            latestReview &&
            captureSave.state.intent.command === 'closeout_decisions'
              ? () => useReviewedCapture(false)
              : undefined
          }
        />
      )}
      {!captureEnabled && (
        <Alert role='note' color='blue' data-testid='closeout-wizard-disabled'>
          {t`Closeout capture, material reconciliation and readings are not enabled. Readiness still applies.`}
          {!accepted && (
            <Text size='sm'>{t`Enter the manual account below. Values remain an unsaved draft until the final update.`}</Text>
          )}
        </Alert>
      )}
      {!captureEnabled && !accepted && (
        <ReviewStep
          capture={null}
          proposal={null}
          captureAvailable={false}
          effectiveFieldValue={effectiveFieldValue}
          setFieldValue={(field, value) => {
            setDraftBase(base());
            setFieldValues((current) => ({ ...current, [field]: value }));
          }}
          onSubmit={() => {}}
          busy={!canComplete || mutationLocked}
          saving={false}
        />
      )}
      <PreparedCloseout
        workOrder={workOrder}
        updateStatus={update.state.status}
        sourcesEnabled={captureEnabled}
        partUsage={
          !captureEnabled || partUsageQuery.isError
            ? undefined
            : partUsageQuery.data
        }
        draftDowntime={
          update.state.status === 'idle'
            ? downtime
            : (update.state.intent.payload.downtime_minutes ?? '')
        }
        draftFollowUp={
          update.state.status === 'idle'
            ? followUpRequired
              ? followUp
              : ''
            : update.state.intent.payload.follow_up
        }
        captures={
          !captureEnabled || capturesQuery.isError
            ? undefined
            : capturesQuery.data
        }
        proposal={proposal}
        accountLoading={
          captureEnabled &&
          (capturesQuery.isFetching ||
            (captureId != null &&
              (proposalQuery.isPending || proposalQuery.isFetching)))
        }
        accountUnavailable={captureEnabled && proposalQuery.isError}
        sourceCapture={accountCapture}
        manualAccepted={
          captureEnabled &&
          !!accepted &&
          capturesQuery.data !== undefined &&
          !capturesQuery.isFetching &&
          !capturesQuery.isError &&
          !accountCapture
        }
        fields={
          update.state.status === 'idle'
            ? Object.fromEntries(
                ['cause', ...REQUIRED_FIELDS].map((field) => [
                  field,
                  effectiveFieldValue(field)
                ])
              )
            : {
                cause: update.state.intent.payload.cause,
                action: update.state.intent.payload.action,
                result: update.state.intent.payload.result,
                verification_summary:
                  update.state.intent.payload.verification_summary
              }
        }
        accepted={accepted}
        amendments={
          !captureEnabled || amendmentsQuery.isError
            ? undefined
            : amendmentsQuery.data
        }
        readings={
          !captureEnabled || readingsQuery.isError
            ? undefined
            : readingsQuery.data
        }
        error={
          captureEnabled &&
          (capturesQuery.isError ||
            proposalQuery.isError ||
            readingsQuery.isError ||
            partUsageQuery.isError ||
            amendmentsQuery.isError)
        }
        changed={changedFields}
        updateAction={
          <>
            {!accepted && (
              <fieldset
                disabled={!canComplete || mutationLocked}
                style={{ border: 0, padding: 0, margin: 0 }}
              >
                <Group mt='md'>
                  <NumberInput
                    label={t`Downtime (minutes)`}
                    description={t`Leave blank when unknown`}
                    min={0}
                    allowDecimal={false}
                    value={downtime}
                    onChange={(value) => {
                      setDraftBase(base());
                      setDowntime(typeof value === 'number' ? value : '');
                    }}
                  />
                  <Checkbox
                    label={t`Follow-up required`}
                    checked={followUpRequired}
                    onChange={(event) => {
                      setDraftBase(base());
                      setFollowUpRequired(event.currentTarget.checked);
                    }}
                  />
                </Group>
                {followUpRequired && (
                  <Textarea
                    label={t`Follow-up`}
                    value={followUp}
                    onChange={(event) => {
                      setDraftBase(base());
                      setFollowUp(event.currentTarget.value);
                    }}
                    autosize
                    minRows={2}
                  />
                )}
              </fieldset>
            )}
            <WorkRecordUpdate
              onRecovery={(next) => {
                setCaptureEditing(true);
                setStep(next);
              }}
              state={update.state}
              latest={Object.fromEntries(
                ['cause', ...REQUIRED_FIELDS].map((field) => [
                  field,
                  savedFieldValue(proposal, field)
                ])
              )}
              version={workOrder.lifecycle_version}
              readiness={readiness}
              loading={readinessQuery.isPending || readinessQuery.isFetching}
              eligible={accountReady && update.available}
              completed={workOrder.lifecycle_status === 'completed'}
              completedAt={accepted?.completed_at}
              completedId={accepted?.id}
              onSubmit={complete}
              onReconcile={() => {
                if (!finalUpdateLocked) return update.execute();
              }}
              onReview={() => {
                return update
                  .reviewLatest()
                  .then((version) => setReviewedVersion(version ?? null));
              }}
              reviewed={
                reviewedVersion != null &&
                reviewedVersion === workOrder.lifecycle_version &&
                !capturesQuery.isFetching &&
                !capturesQuery.isError &&
                !proposalQuery.isFetching &&
                !proposalQuery.isError
              }
              onUseReviewed={() => {
                const retained =
                  update.state.status !== 'idle'
                    ? update.state.intent.payload
                    : {};
                // Local edits win for fields without a newer saved human decision.
                // Saved decisions are displayed in the comparison and remain authoritative.
                const recovered = reviewedAccountValues(
                  proposal,
                  retained,
                  fieldValues
                );
                void update.releaseRefused().then((released) => {
                  if (released) {
                    setFieldValues(recovered);
                    setDraftBase(null);
                    setReviewedVersion(null);
                  }
                });
              }}
              locked={finalUpdateLocked}
            />
          </>
        }
        reconciliation={
          execution && <CloseoutReconciliation workOrder={execution} />
        }
      />
      {execution && <ServiceRestoration workOrder={execution} />}
      {effectsQuery.data && effectsQuery.data.length > 0 && (
        <details>
          <summary>{t`Recorded follow-up effects`}</summary>
          {effectsQuery.data.map((effect) => (
            <Text size='sm' key={displayUpdate(effect).source.id}>
              {effect.effect_type} · {displayUpdate(effect).state} ·{' '}
              {t`Attempts`}: {effect.attempts}
            </Text>
          ))}
        </details>
      )}
      {captureEnabled &&
        !accepted &&
        !['pending', 'uncertain', 'confirmed'].includes(
          update.state.status
        ) && (
          <Stack gap='sm'>
            {(captureInProgress || dictateOpen) && (
              <Alert color='yellow' data-testid='closeout-capture-lock'>
                {dictateOpen
                  ? t`Finish or cancel dictation before changing the account or updating the maintenance record.`
                  : t`Finish the active capture review before updating the maintenance record. The original narrative is preserved.`}
              </Alert>
            )}
            <Card
              component='details'
              withBorder
              open={captureEditing}
              onToggle={(event) => setCaptureEditing(event.currentTarget.open)}
              data-testid='closeout-capture-disclosure'
            >
              <summary
                style={{ cursor: 'pointer', fontWeight: 600 }}
              >{t`Edit or add to the capture`}</summary>
              <Stepper
                mt='md'
                id='closeout-recovery'
                active={step}
                onStepClick={setStep}
                size='sm'
                allowNextStepsSelect
              >
                <Stepper.Step
                  label={t`Narrative`}
                  icon={<IconSparkles size={16} />}
                >
                  <NarrativeStep
                    capture={capture}
                    narrative={narrative}
                    voiceReason={voiceReason}
                    setVoiceReason={(value) => {
                      setDraftBase(base());
                      setVoiceReason(value);
                    }}
                    setNarrative={(value) => {
                      setDraftBase(base());
                      setNarrative(value);
                    }}
                    onCreate={createCapture}
                    onExtract={extract}
                    onDictate={() => setDictateOpen(true)}
                    busy={mutationLocked}
                    canCapture={canCapture}
                    saving={busy || captureSave.state?.status === 'pending'}
                    loading={capturesQuery.isLoading}
                  />
                </Stepper.Step>
                <Stepper.Step
                  label={t`Review`}
                  icon={<IconListCheck size={16} />}
                >
                  <ReviewStep
                    capture={capture}
                    proposal={proposal ?? null}
                    effectiveFieldValue={effectiveFieldValue}
                    setFieldValue={(field, value) => {
                      setDraftBase(base());
                      setFieldValues((current) => ({
                        ...Object.fromEntries(
                          ['cause', ...REQUIRED_FIELDS].map((key) => [
                            key,
                            effectiveFieldValue(key)
                          ])
                        ),
                        ...current,
                        [field]: value
                      }));
                    }}
                    onSubmit={submitDecisions}
                    busy={
                      (capture ? !canReview : !canComplete) ||
                      mutationLocked ||
                      (capture != null && !decisionToken)
                    }
                    saving={busy || captureSave.state?.status === 'pending'}
                  />
                </Stepper.Step>
                <Stepper.Step label={t`Parts`}>
                  <PartsStep
                    rows={partUsageQuery.data ?? []}
                    drafts={partDrafts}
                    onDraft={(row, values) =>
                      setPartDrafts((current) => ({
                        ...current,
                        [row.id]: {
                          ...(current[row.id] ?? {
                            disposition: '',
                            reason: ''
                          }),
                          version: current[row.id]?.version ?? row.version,
                          ...values
                        }
                      }))
                    }
                    onRefresh={refreshPartUsage}
                    onResolve={resolveRow}
                    canRefresh={canCapture}
                    canResolve={canReconcile}
                    busy={mutationLocked || partFailure != null}
                    saving={busy}
                  />
                </Stepper.Step>
              </Stepper>
            </Card>
          </Stack>
        )}
    </Stack>
  );
}

function NarrativeStep({
  capture,
  narrative,
  setNarrative,
  onCreate,
  voiceReason,
  setVoiceReason,
  onExtract,
  onDictate,
  busy,
  canCapture,
  saving,
  loading
}: Readonly<{
  capture: CloseoutCapture | null;
  narrative: string;
  setNarrative: (value: string) => void;
  onCreate: () => void;
  voiceReason: string;
  setVoiceReason: (value: string) => void;
  onExtract: () => void;
  onDictate: () => void;
  busy: boolean;
  canCapture: boolean;
  saving: boolean;
  loading: boolean;
}>) {
  const resultFocus = useActionFocus(busy || saving || loading);
  if (loading) return <Loader size='sm' />;
  if (capture) {
    return (
      <Stack
        {...resultFocus.props}
        aria-label={t`Closeout story recovery`}
        gap='sm'
        mt='md'
      >
        <Group gap='xs'>
          <Badge>{capture.status}</Badge>
          {capture.source_type === 'voice' && (
            <Badge color='grape'>{t`Voice transcript`}</Badge>
          )}
          <Text size='sm' c='dimmed'>
            {t`Revision`} {capture.revision ?? t`Not recorded`}
          </Text>
        </Group>
        <Textarea
          aria-label={t`Saved capture narrative`}
          value={capture.narrative}
          readOnly
          autosize
          minRows={4}
        />
        {capture.status === 'open' && (
          <Group>
            <Button
              onClick={() => resultFocus.run(onExtract)}
              loading={saving}
              disabled={busy || !canCapture}
              leftSection={<IconSparkles size={16} />}
            >
              {t`Extract structured draft`}
            </Button>
            <Text size='xs' c='dimmed'>
              {t`Manual entry stays available if extraction is unavailable.`}
            </Text>
          </Group>
        )}
      </Stack>
    );
  }
  return (
    <Stack
      {...resultFocus.props}
      aria-label={t`Closeout story recovery`}
      gap='sm'
      mt='md'
    >
      <Text size='sm'>{t`Use Dictate for the default voice-transcript path, or record a typed equivalent and why voice cannot be used. Attach original audio in Dictate before accepting the transcript.`}</Text>
      <TextInput
        label={t`Why voice is unavailable or unusable`}
        description={t`Retained with the typed story and its recorder. Required for a new typed fallback.`}
        value={voiceReason}
        disabled={busy}
        maxLength={500}
        onChange={(event) => setVoiceReason(event.currentTarget.value)}
      />
      <Textarea
        label={t`What did you do?`}
        disabled={busy}
        description={t`Describe the work in your own words; you will review every extracted field before anything becomes record.`}
        placeholder={t`Found the inlet filter clogged, replaced it with a new one from the kit, flow back to 20 GPM...`}
        value={narrative}
        onChange={(event) => setNarrative(event.currentTarget.value)}
        autosize
        minRows={5}
      />
      <Group>
        <Button
          onClick={() => resultFocus.run(onCreate)}
          loading={saving}
          disabled={
            !canCapture || busy || !narrative.trim() || !voiceReason.trim()
          }
        >
          {t`Capture narrative`}
        </Button>
        {/* B4: voice provenance path — mutually exclusive with typing,
            because the server refuses a handoff over an active capture. */}
        <Button
          variant='filled'
          color='grape'
          onClick={onDictate}
          disabled={!canCapture || busy || !!narrative.trim()}
          data-testid='dictate-closeout'
        >
          {t`Dictate`}
        </Button>
      </Group>
    </Stack>
  );
}

function ReviewStep({
  capture,
  proposal,
  effectiveFieldValue,
  setFieldValue,
  onSubmit,
  busy,
  saving,
  captureAvailable = true
}: Readonly<{
  capture: CloseoutCapture | null;
  captureAvailable?: boolean;
  proposal: CloseoutProposal | null;
  effectiveFieldValue: (field: string) => string;
  setFieldValue: (field: string, value: string) => void;
  onSubmit: () => void;
  busy: boolean;
  saving: boolean;
}>) {
  const resultFocus = useActionFocus(busy || saving);
  const fields = ['cause', ...REQUIRED_FIELDS];
  return (
    <Stack
      {...resultFocus.props}
      aria-label={t`Closeout field review`}
      gap='sm'
      mt='md'
    >
      {!capture && captureAvailable && (
        <Alert
          role='note'
          color='gray'
        >{t`Manual account recovery: values remain an unsaved draft until the final update. A technician story can be added separately through the native capture.`}</Alert>
      )}
      {proposal?.warnings?.length ? (
        <Alert color='yellow' icon={<IconAlertTriangle size={16} />}>
          {proposal.warnings.join(', ')}
        </Alert>
      ) : null}
      {fields.map((field) => {
        const proposed = proposal?.fields?.[field];
        const required = (REQUIRED_FIELDS as readonly string[]).includes(field);
        return (
          <Card withBorder key={field} padding='sm'>
            <Group justify='space-between' mb='xs'>
              <Text fw={600} size='sm'>
                {FIELD_LABELS[field] ?? field}
                {required ? ' *' : ''}
              </Text>
              {proposed ? (
                <Badge
                  color={proposed.confidence >= 0.8 ? 'green' : 'yellow'}
                  variant='light'
                >
                  {t`confidence`} {Math.round(proposed.confidence * 100)}%
                </Badge>
              ) : (
                <Badge color='gray' variant='light'>{t`manual`}</Badge>
              )}
            </Group>
            {proposed?.warnings?.length ? (
              <Text size='xs' c='orange' mb='xs'>
                {proposed.warnings.join(', ')}
              </Text>
            ) : null}
            {capture &&
              narrativeExcerpts(capture.narrative, proposed).map((span) => (
                <Text key={`${span.start}-${span.end}`} size='xs' c='dimmed'>
                  {t`Narrative code points`} [{span.start}, {span.end}) · “
                  {span.text}”
                </Text>
              ))}
            <Textarea
              disabled={busy}
              value={effectiveFieldValue(field)}
              onChange={(event) =>
                setFieldValue(field, event.currentTarget.value)
              }
              autosize
              minRows={2}
              aria-label={FIELD_LABELS[field] ?? field}
            />
          </Card>
        );
      })}
      {proposal?.part_candidates?.length ? (
        <Alert color='blue'>
          {t`Narrative mentions parts:`}{' '}
          {proposal.part_candidates
            .map((candidate) => candidate.text)
            .join('; ')}
          {' — '}
          {t`bind or dismiss them in the Parts step.`}
        </Alert>
      ) : null}
      <Group>
        {capture && (
          <Button
            onClick={() => resultFocus.run(onSubmit)}
            loading={saving}
            disabled={busy}
          >
            {t`Record decisions`}
          </Button>
        )}
        {capture?.status === 'reviewed' && (
          <Badge color='green' leftSection={<IconCircleCheck size={12} />}>
            {t`Reviewed`}
          </Badge>
        )}
      </Group>
    </Stack>
  );
}

function PartsStep({
  rows,
  onRefresh,
  onResolve,
  canRefresh,
  canResolve,
  busy,
  saving,
  drafts,
  onDraft
}: Readonly<{
  rows: PartUsageRow[];
  drafts: Record<
    number,
    { disposition: string; reason: string; version: number }
  >;
  onDraft: (
    row: PartUsageRow,
    values: { disposition?: string; reason?: string }
  ) => void;
  onRefresh: () => void;
  onResolve: (row: PartUsageRow, disposition: string, reason: string) => void;
  canRefresh: boolean;
  canResolve: boolean;
  busy: boolean;
  saving: boolean;
}>) {
  const resultFocus = useActionFocus(busy || saving);
  return (
    <Stack
      {...resultFocus.props}
      aria-label={t`Material reconciliation recovery`}
      gap='sm'
      mt='md'
    >
      <Group>
        <Button
          variant='light'
          onClick={() => resultFocus.run(onRefresh)}
          loading={saving}
          disabled={!canRefresh || busy}
          leftSection={<IconRefresh size={16} />}
        >
          {t`Refresh from custody truth`}
        </Button>
      </Group>
      {rows.length === 0 ? (
        <Text size='sm' c='dimmed'>
          {t`No usage rows yet. Refresh to seed rows from the job kit.`}
        </Text>
      ) : (
        <WorkTableScroll
          minWidth={640}
          label={t`Material reconciliation table, scroll for all columns`}
        >
          <Table striped withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t`Source`}</Table.Th>
                <Table.Th>{t`Planned`}</Table.Th>
                <Table.Th>{t`Issued`}</Table.Th>
                <Table.Th>{t`Used`}</Table.Th>
                <Table.Th>{t`State`}</Table.Th>
                <Table.Th>{t`Resolution`}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr key={row.id}>
                  <Table.Td>
                    {row.source === 'narrative'
                      ? `${t`Candidate`}: ${row.candidate_text}`
                      : row.source}
                  </Table.Td>
                  <Table.Td>{row.planned_quantity ?? t`Not recorded`}</Table.Td>
                  <Table.Td>{row.issued_quantity ?? t`Not recorded`}</Table.Td>
                  <Table.Td>{row.used_quantity ?? t`Not recorded`}</Table.Td>
                  <Table.Td>
                    <Badge
                      color={row.state === 'reconciled' ? 'green' : 'yellow'}
                      variant='light'
                    >
                      {row.disposition || row.state}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    {row.state === 'reconciled' ? (
                      <IconCircleCheck size={16} color='green' />
                    ) : (
                      <Group gap='xs' wrap='nowrap'>
                        <Select
                          disabled={!canResolve || busy}
                          size='xs'
                          placeholder={t`Disposition`}
                          data={
                            row.source === 'narrative'
                              ? ['dismissed']
                              : [
                                  'consumed',
                                  'returned',
                                  'scrapped',
                                  'spare_installed',
                                  'serialized_manual',
                                  'correction'
                                ]
                          }
                          value={drafts[row.id]?.disposition ?? null}
                          onChange={(value) =>
                            onDraft(row, { disposition: value ?? '' })
                          }
                        />
                        <TextInput
                          disabled={!canResolve || busy}
                          size='xs'
                          placeholder={t`Reason`}
                          value={drafts[row.id]?.reason ?? ''}
                          onChange={(event) =>
                            onDraft(row, { reason: event.currentTarget.value })
                          }
                        />
                        <Button
                          size='xs'
                          variant='light'
                          disabled={
                            !canResolve || !drafts[row.id]?.disposition || busy
                          }
                          onClick={() =>
                            resultFocus.run(() =>
                              onResolve(
                                row,
                                drafts[row.id].disposition,
                                drafts[row.id]?.reason ?? ''
                              )
                            )
                          }
                        >
                          {t`Resolve`}
                        </Button>
                      </Group>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </WorkTableScroll>
      )}
    </Stack>
  );
}

export function ReadingsStep({
  workOrderId,
  rows,
  runCommand,
  busy
}: Readonly<{
  workOrderId: number;
  rows: ReadingRow[];
  runCommand: (fn: () => Promise<void>) => Promise<void>;
  busy: boolean;
}>) {
  const api = useApi();
  const [label, setLabel] = useState('');
  const [rawText, setRawText] = useState('');
  const [unit, setUnit] = useState('');
  const [required, setRequired] = useState(false);
  const resultFocus = useActionFocus(busy);

  const addReading = () =>
    resultFocus.run(() =>
      runCommand(async () => {
        const response = await api.post(
          apiUrl(ApiEndpoints.work_order_closeout_readings, workOrderId),
          { label, raw_text: rawText, unit, required }
        );
        if (response.status !== 201 || !Number.isSafeInteger(response.data?.id))
          throw new Error('Reading save unconfirmed');
        setLabel('');
        setRawText('');
      })
    );

  return (
    <Stack
      {...resultFocus.props}
      aria-label={t`Reading entry`}
      gap='sm'
      mt='md'
    >
      {rows.map((row) => (
        <Group key={row.id} gap='xs'>
          <Badge
            color={
              row.verification_state === 'verified'
                ? 'gray'
                : row.verification_state === 'failed'
                  ? 'red'
                  : 'yellow'
            }
            variant='light'
          >
            {readingStatusLabel(row.verification_state)}
          </Badge>
          <Text size='sm'>
            {row.label}: {row.raw_text || t`Not recorded`}{' '}
            {row.unit && `(${row.unit})`}
          </Text>
          {row.required && <Badge size='xs'>{t`required`}</Badge>}
          {row.warnings.includes('numeric_ambiguity') && (
            <WarningText size='xs'>{t`ambiguous numeric — correct or re-enter`}</WarningText>
          )}
        </Group>
      ))}
      <Divider />
      <Group align='end'>
        <TextInput
          label={t`Label`}
          disabled={busy}
          value={label}
          onChange={(event) => setLabel(event.currentTarget.value)}
          size='xs'
        />
        <TextInput
          label={t`Value`}
          disabled={busy}
          value={rawText}
          onChange={(event) => setRawText(event.currentTarget.value)}
          size='xs'
        />
        <TextInput
          label={t`Unit`}
          disabled={busy}
          value={unit}
          onChange={(event) => setUnit(event.currentTarget.value)}
          size='xs'
        />
        <Checkbox
          label={t`Required`}
          disabled={busy}
          checked={required}
          onChange={(event) => setRequired(event.currentTarget.checked)}
        />
        <Button size='xs' onClick={addReading} disabled={!label.trim() || busy}>
          {t`Add reading`}
        </Button>
      </Group>
    </Stack>
  );
}

/** Physical reading entry lives in Verify & Return; Closeout reads the same cache. */
export function WorkCloseoutReadings({
  workOrderId,
  readOnly = false
}: Readonly<{ workOrderId: number; readOnly?: boolean }>) {
  const api = useApi();
  const user = useUserState((state) => state.user);
  const canCapture =
    !!user?.is_superuser || !!user?.permissions?.closeout?.includes('capture');
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<'refused' | 'uncertain' | null>(null);
  const query = useQuery({
    queryKey: ['closeout-readings', workOrderId],
    queryFn: async () =>
      (
        await api.get<ReadingRow[]>(
          apiUrl(ApiEndpoints.work_order_closeout_readings, workOrderId)
        )
      ).data
  });
  const runCommand = async (fn: () => Promise<void>) => {
    if (!canCapture || busy || failure === 'uncertain') return;
    setBusy(true);
    setFailure(null);
    try {
      await fn();
      await queryClient.invalidateQueries({
        queryKey: ['closeout-readings', workOrderId]
      });
      await queryClient.invalidateQueries({
        queryKey: ['work-order-readiness', workOrderId, 'complete']
      });
      await queryClient.invalidateQueries({
        queryKey: ['work-order', workOrderId]
      });
    } catch (error) {
      const status = (error as any)?.response?.status;
      setFailure(
        status >= 400 && status < 500 && ![408, 429].includes(status)
          ? 'refused'
          : 'uncertain'
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card withBorder>
      <Title order={2} size='h4'>{t`Closeout readings`}</Title>
      {failure && (
        <Alert color='orange'>
          {failure === 'uncertain'
            ? t`Reading save not confirmed. Your draft is retained. This append has no safe automatic retry; reconcile the native readings before recording it again.`
            : t`Reading save refused. Your draft is retained. Check access and required fields before retrying.`}
        </Alert>
      )}
      {query.isError && query.data && (
        <Alert color='orange'>{t`Latest readings unavailable. Last-known records and draft are retained; entry is locked.`}</Alert>
      )}
      {query.isPending ? (
        <Loader size='sm' />
      ) : !query.data ? (
        <Alert color='red'>
          {t`Readings could not be read.`}
          <Button
            variant='light'
            onClick={() => void query.refetch()}
          >{t`Retry`}</Button>
        </Alert>
      ) : readOnly || !canCapture ? (
        <Stack gap='xs'>
          {query.data.map((row) => (
            <Text key={row.id} size='sm'>
              {row.label}: {row.raw_text || t`Not recorded`} {row.unit} ·{' '}
              {readingStatusLabel(row.verification_state)}
            </Text>
          ))}
          <Text size='xs' c='dimmed'>
            {readOnly
              ? t`This work is completed. Further physical investigation requires the supported linked or reopened work flow.`
              : t`Closeout capture permission is required to record readings.`}
          </Text>
        </Stack>
      ) : (
        <ReadingsStep
          workOrderId={workOrderId}
          rows={query.data}
          busy={busy || failure === 'uncertain' || query.isError}
          runCommand={runCommand}
        />
      )}
    </Card>
  );
}
