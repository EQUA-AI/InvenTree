import type { CloseoutAmendment } from '@lib/types/Closeout';
import type { StructuredCloseoutOverview } from '@lib/types/WorkOrderOverview';
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
import DictateCloseoutModal from './DictateCloseoutModal';
import { WorkRecordUpdate } from './FinalUpdateAction';
import { PreparedCloseout } from './PreparedCloseout';
import { fieldDecisions, savedFieldValue } from './closeoutAccount';
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
import { showApiErrorMessage } from '../../../functions/notifications';

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
  accepted
}: Readonly<{
  workOrder: CloseoutWorkOrder;
  accepted?: StructuredCloseoutOverview | null;
}>) {
  const api = useApi();
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);
  const [captureEditing, setCaptureEditing] = useState(false);
  const [narrative, setNarrative] = useState('');
  const [dictateOpen, setDictateOpen] = useState(false);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [downtime, setDowntime] = useState<number | ''>('');
  const [followUp, setFollowUp] = useState('');
  const [followUpRequired, setFollowUpRequired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reviewedVersion, setReviewedVersion] = useState<number | null>(null);

  const woId = workOrder.id;

  const capturesQuery = useQuery({
    queryKey: ['closeout-captures', woId],
    queryFn: async () => {
      const response = await api.get<CloseoutCapture[]>(
        apiUrl(ApiEndpoints.work_order_closeout_captures, woId)
      );
      return response.data;
    }
  });

  const capture = useMemo(
    () =>
      (capturesQuery.data ?? []).find((row) =>
        ACTIVE_CAPTURE_STATES.includes(row.status)
      ) ?? null,
    [capturesQuery.data]
  );

  const captureId = capture?.id ?? null;
  const proposalQuery = useQuery({
    queryKey: ['closeout-proposal', woId, captureId],
    enabled:
      captureId != null &&
      ['proposed', 'reviewed'].includes(capture?.status ?? ''),
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

  const partUsageQuery = useQuery({
    queryKey: ['closeout-part-usage', woId],
    queryFn: async () => {
      const response = await api.get<PartUsageRow[]>(
        apiUrl(ApiEndpoints.work_order_closeout_part_usage, woId)
      );
      return response.data;
    }
  });

  const readingsQuery = useQuery({
    queryKey: ['closeout-readings', woId],
    queryFn: async () => {
      const response = await api.get<ReadingRow[]>(
        apiUrl(ApiEndpoints.work_order_closeout_readings, woId)
      );
      return response.data;
    }
  });

  const readinessQuery = useQuery({
    queryKey: ['work-order-readiness', woId, 'complete'],
    queryFn: async () => {
      const response = await api.get<Readiness>(
        `${apiUrl(ApiEndpoints.work_order_readiness, woId)}?action=complete`
      );
      return response.data;
    }
  });

  const amendmentsQuery = useQuery({
    queryKey: ['closeout-amendments', woId],
    enabled: accepted != null,
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

  const update = useWorkRecordUpdate(woId, invalidate);
  const mutationLocked =
    busy ||
    dictateOpen ||
    ['pending', 'uncertain', 'confirmed'].includes(update.state.status);
  const effectsQuery = useQuery({
    queryKey: ['closeout-effects', woId],
    enabled: update.state.status === 'confirmed' || accepted != null,
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
      try {
        await fn();
        invalidate();
      } catch (error) {
        showApiErrorMessage({ error, title: t`Closeout command failed` });
      } finally {
        setBusy(false);
      }
    },
    [invalidate, mutationLocked]
  );

  const createCapture = () =>
    runCommand(async () => {
      await api.post(apiUrl(ApiEndpoints.work_order_closeout_captures, woId), {
        expected_version: workOrder.lifecycle_version,
        idempotency_key: newIdempotencyKey(),
        narrative
      });
      notifications.show({
        message: t`Narrative captured`,
        color: 'green'
      });
    });

  const extract = () =>
    runCommand(async () => {
      if (!capture) return;
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
      fieldValues[field] ?? savedFieldValue(proposalQuery.data, field),
    [fieldValues, proposalQuery.data]
  );

  const submitDecisions = () =>
    runCommand(async () => {
      if (!capture) return;
      const decisions = fieldDecisions(
        proposalQuery.data,
        Object.fromEntries(
          ['cause', ...REQUIRED_FIELDS].map((field) => [
            field,
            effectiveFieldValue(field)
          ])
        )
      );
      await api.post(
        apiUrl(ApiEndpoints.work_order_closeout_capture_decisions, woId, {
          capId: capture.id
        }),
        {
          expected_version: workOrder.lifecycle_version,
          idempotency_key: newIdempotencyKey(),
          decisions
        }
      );
      notifications.show({
        message: t`Field decisions recorded`,
        color: 'green'
      });
    });

  const refreshPartUsage = () =>
    runCommand(async () => {
      await api.post(
        apiUrl(ApiEndpoints.work_order_closeout_part_usage_refresh, woId),
        {}
      );
    });

  const resolveRow = (row: PartUsageRow, disposition: string, reason: string) =>
    runCommand(async () => {
      await api.post(
        apiUrl(ApiEndpoints.work_order_closeout_part_usage_resolve, woId, {
          rowId: row.id
        }),
        {
          disposition,
          reason,
          expected_row_version: row.version
        }
      );
    });

  const accountReady =
    REQUIRED_FIELDS.every(
      (field) => effectiveFieldValue(field).trim().length > 0
    ) &&
    (!capture ||
      (capture.status === 'reviewed' &&
        REQUIRED_FIELDS.every(
          (field) =>
            effectiveFieldValue(field) ===
            savedFieldValue(proposalQuery.data, field)
        ))) &&
    !capturesQuery.isPending &&
    !capturesQuery.isError &&
    !proposalQuery.isFetching &&
    !proposalQuery.isError &&
    !partUsageQuery.isPending &&
    !partUsageQuery.isError &&
    !readingsQuery.isPending &&
    !readingsQuery.isError;
  const complete = () => {
    if (
      !accountReady ||
      mutationLocked ||
      !readinessQuery.data?.ready ||
      workOrder.lifecycle_status === 'completed'
    )
      return;
    const payload: UpdateIntent['payload'] = {
      expected_version: workOrder.lifecycle_version,
      idempotency_key: newIdempotencyKey(),
      action: effectiveFieldValue('action'),
      result: effectiveFieldValue('result'),
      verification_summary: effectiveFieldValue('verification_summary'),
      cause: effectiveFieldValue('cause'),
      follow_up: followUp,
      follow_up_required: followUpRequired,
      ...(downtime !== '' ? { downtime_minutes: downtime } : {}),
      ...(capture?.status === 'reviewed' ? { capture_id: capture.id } : {})
    };
    void update.execute({ workOrderId: woId, payload });
  };

  const readiness = readinessQuery.data;
  const changedFields = Object.entries(fieldValues).some(
    ([key, value]) => value !== savedFieldValue(proposalQuery.data, key)
  );
  const captureInProgress = capture != null && capture.status !== 'reviewed';
  const hasCaptureDraft = !!narrative.trim() || changedFields;
  useEffect(() => {
    // A ready, saved account is direct; unfinished capture work is visible.
    // Once opened, native panel retention also preserves this disclosure state.
    if (captureInProgress || dictateOpen || hasCaptureDraft)
      setCaptureEditing(true);
  }, [captureInProgress, dictateOpen, hasCaptureDraft]);

  return (
    <Card withBorder padding='lg'>
      <Title order={4} mb='md'>{t`Closeout`}</Title>
      <DictateCloseoutModal
        opened={dictateOpen}
        onClose={() => setDictateOpen(false)}
        workOrderId={workOrder.id}
        workOrderVersion={workOrder.lifecycle_version}
        onCommitted={() => void invalidate()}
      />
      <PreparedCloseout
        workOrder={workOrder}
        updateStatus={update.state.status}
        partUsage={partUsageQuery.isError ? undefined : partUsageQuery.data}
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
        captures={capturesQuery.isError ? undefined : capturesQuery.data}
        proposal={proposalQuery.data}
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
        amendments={amendmentsQuery.isError ? undefined : amendmentsQuery.data}
        readings={readingsQuery.isError ? undefined : readingsQuery.data}
        error={
          capturesQuery.isError ||
          proposalQuery.isError ||
          readingsQuery.isError ||
          partUsageQuery.isError ||
          amendmentsQuery.isError
        }
        changed={changedFields}
      />
      {!accepted && (
        <fieldset
          disabled={mutationLocked}
          style={{ border: 0, padding: 0, margin: 0 }}
        >
          <Group mt='md'>
            <NumberInput
              label={t`Downtime (minutes)`}
              description={t`Leave blank when unknown`}
              min={0}
              value={downtime}
              onChange={(value) =>
                setDowntime(typeof value === 'number' ? value : '')
              }
            />
            <Checkbox
              label={t`Follow-up required`}
              checked={followUpRequired}
              onChange={(event) =>
                setFollowUpRequired(event.currentTarget.checked)
              }
            />
          </Group>
          {followUpRequired && (
            <Textarea
              label={t`Follow-up`}
              value={followUp}
              onChange={(event) => setFollowUp(event.currentTarget.value)}
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
          REQUIRED_FIELDS.map((field) => [
            field,
            savedFieldValue(proposalQuery.data, field)
          ])
        )}
        version={workOrder.lifecycle_version}
        readiness={readiness}
        loading={readinessQuery.isPending || readinessQuery.isFetching}
        eligible={accountReady && update.available}
        completed={workOrder.lifecycle_status === 'completed'}
        completedAt={accepted?.completed_at}
        onSubmit={complete}
        onReconcile={() => void update.execute()}
        onReview={() => {
          void update
            .reviewLatest()
            .then((version) => setReviewedVersion(version ?? null));
        }}
        reviewed={
          reviewedVersion != null &&
          reviewedVersion === workOrder.lifecycle_version
        }
        onUseReviewed={() => {
          void update.releaseRefused().then((released) => {
            if (released) {
              setFieldValues({});
              setReviewedVersion(null);
            }
          });
        }}
        locked={busy || dictateOpen}
      />
      {effectsQuery.data && effectsQuery.data.length > 0 && (
        <details>
          <summary>{t`Recorded follow-up effects`}</summary>
          {effectsQuery.data.map((effect) => (
            <Text size='sm' key={effect.id}>
              {effect.effect_type} · {effect.status} · {t`Attempts`}:{' '}
              {effect.attempts}
            </Text>
          ))}
        </details>
      )}
      {!accepted &&
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
                    setNarrative={setNarrative}
                    onCreate={createCapture}
                    onExtract={extract}
                    onDictate={() => setDictateOpen(true)}
                    busy={mutationLocked}
                    loading={capturesQuery.isLoading}
                  />
                </Stepper.Step>
                <Stepper.Step
                  label={t`Review`}
                  icon={<IconListCheck size={16} />}
                >
                  <ReviewStep
                    capture={capture}
                    proposal={proposalQuery.data ?? null}
                    effectiveFieldValue={effectiveFieldValue}
                    setFieldValue={(field, value) =>
                      setFieldValues((current) => ({
                        ...current,
                        [field]: value
                      }))
                    }
                    onSubmit={submitDecisions}
                    busy={mutationLocked}
                  />
                </Stepper.Step>
                <Stepper.Step label={t`Parts`}>
                  <PartsStep
                    rows={partUsageQuery.data ?? []}
                    onRefresh={refreshPartUsage}
                    onResolve={resolveRow}
                    busy={mutationLocked}
                  />
                </Stepper.Step>
              </Stepper>
            </Card>
          </Stack>
        )}
    </Card>
  );
}

function NarrativeStep({
  capture,
  narrative,
  setNarrative,
  onCreate,
  onExtract,
  onDictate,
  busy,
  loading
}: Readonly<{
  capture: CloseoutCapture | null;
  narrative: string;
  setNarrative: (value: string) => void;
  onCreate: () => void;
  onExtract: () => void;
  onDictate: () => void;
  busy: boolean;
  loading: boolean;
}>) {
  if (loading) return <Loader size='sm' />;
  if (capture) {
    return (
      <Stack gap='sm' mt='md'>
        <Group gap='xs'>
          <Badge>{capture.status}</Badge>
          {capture.source_type === 'voice' && (
            <Badge color='grape'>{t`Voice transcript`}</Badge>
          )}
          <Text size='sm' c='dimmed'>
            {t`Revision`} {capture.revision ?? '-'}
          </Text>
        </Group>
        <Textarea value={capture.narrative} readOnly autosize minRows={4} />
        {capture.status === 'open' && (
          <Group>
            <Button
              onClick={onExtract}
              loading={busy}
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
    <Stack gap='sm' mt='md'>
      <Textarea
        label={t`What did you do?`}
        description={t`Describe the work in your own words; you will review every extracted field before anything becomes record.`}
        placeholder={t`Found the inlet filter clogged, replaced it with a new one from the kit, flow back to 20 GPM...`}
        value={narrative}
        onChange={(event) => setNarrative(event.currentTarget.value)}
        autosize
        minRows={5}
      />
      <Group>
        <Button onClick={onCreate} loading={busy} disabled={!narrative.trim()}>
          {t`Capture narrative`}
        </Button>
        {/* B4: voice provenance path — mutually exclusive with typing,
            because the server refuses a handoff over an active capture. */}
        <Button
          variant='light'
          color='grape'
          onClick={onDictate}
          disabled={busy || !!narrative.trim()}
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
  busy
}: Readonly<{
  capture: CloseoutCapture | null;
  proposal: CloseoutProposal | null;
  effectiveFieldValue: (field: string) => string;
  setFieldValue: (field: string, value: string) => void;
  onSubmit: () => void;
  busy: boolean;
}>) {
  const fields = ['cause', ...REQUIRED_FIELDS];
  return (
    <Stack gap='sm' mt='md'>
      {!capture && (
        <Alert color='gray'>{t`Manual account recovery: values remain an unsaved draft until the final update. A technician story can be added separately through the native capture.`}</Alert>
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
            <Textarea
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
          <Button onClick={onSubmit} loading={busy}>
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
  busy
}: Readonly<{
  rows: PartUsageRow[];
  onRefresh: () => void;
  onResolve: (row: PartUsageRow, disposition: string, reason: string) => void;
  busy: boolean;
}>) {
  const [dispositions, setDispositions] = useState<Record<number, string>>({});
  const [reasons, setReasons] = useState<Record<number, string>>({});
  return (
    <Stack gap='sm' mt='md'>
      <Group>
        <Button
          variant='light'
          onClick={onRefresh}
          loading={busy}
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
                <Table.Td>{row.planned_quantity ?? '-'}</Table.Td>
                <Table.Td>{row.issued_quantity ?? '-'}</Table.Td>
                <Table.Td>{row.used_quantity ?? '-'}</Table.Td>
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
                        value={dispositions[row.id] ?? null}
                        onChange={(value) =>
                          setDispositions((current) => ({
                            ...current,
                            [row.id]: value ?? ''
                          }))
                        }
                      />
                      <TextInput
                        size='xs'
                        placeholder={t`Reason`}
                        value={reasons[row.id] ?? ''}
                        onChange={(event) =>
                          setReasons((current) => ({
                            ...current,
                            [row.id]: event.currentTarget.value
                          }))
                        }
                      />
                      <Button
                        size='xs'
                        variant='light'
                        disabled={!dispositions[row.id] || busy}
                        onClick={() =>
                          onResolve(
                            row,
                            dispositions[row.id],
                            reasons[row.id] ?? ''
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

  const addReading = () =>
    runCommand(async () => {
      await api.post(
        apiUrl(ApiEndpoints.work_order_closeout_readings, workOrderId),
        { label, raw_text: rawText, unit, required }
      );
      setLabel('');
      setRawText('');
    });

  return (
    <Stack gap='sm' mt='md'>
      {rows.map((row) => (
        <Group key={row.id} gap='xs'>
          <Badge
            color={
              row.verification_state === 'verified'
                ? 'green'
                : row.verification_state === 'failed'
                  ? 'red'
                  : 'yellow'
            }
            variant='light'
          >
            {row.verification_state}
          </Badge>
          <Text size='sm'>
            {row.label}: {row.raw_text || '-'} {row.unit && `(${row.unit})`}
          </Text>
          {row.required && <Badge size='xs'>{t`required`}</Badge>}
          {row.warnings.includes('numeric_ambiguity') && (
            <Text
              size='xs'
              c='orange'
            >{t`ambiguous numeric — correct or re-enter`}</Text>
          )}
        </Group>
      ))}
      <Divider />
      <Group align='end'>
        <TextInput
          label={t`Label`}
          value={label}
          onChange={(event) => setLabel(event.currentTarget.value)}
          size='xs'
        />
        <TextInput
          label={t`Value`}
          value={rawText}
          onChange={(event) => setRawText(event.currentTarget.value)}
          size='xs'
        />
        <TextInput
          label={t`Unit`}
          value={unit}
          onChange={(event) => setUnit(event.currentTarget.value)}
          size='xs'
        />
        <Checkbox
          label={t`Required`}
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
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
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
    setBusy(true);
    try {
      await fn();
      await queryClient.invalidateQueries({
        queryKey: ['closeout-readings', workOrderId]
      });
      await queryClient.invalidateQueries({
        queryKey: ['work-order-readiness', workOrderId, 'complete']
      });
    } catch (error) {
      showApiErrorMessage({ error, title: t`Reading could not be recorded` });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card withBorder>
      <Title order={4}>{t`Closeout readings`}</Title>
      {query.isPending ? (
        <Loader size='sm' />
      ) : query.isError ? (
        <Alert color='red'>
          {t`Readings could not be read.`}
          <Button
            variant='light'
            onClick={() => void query.refetch()}
          >{t`Retry`}</Button>
        </Alert>
      ) : readOnly ? (
        <Stack gap='xs'>
          {query.data.map((row) => (
            <Text key={row.id} size='sm'>
              {row.label}: {row.raw_text || t`Not recorded`} {row.unit} ·{' '}
              {row.verification_state}
            </Text>
          ))}
          <Text
            size='xs'
            c='dimmed'
          >{t`This work is completed. Further physical investigation requires the supported linked or reopened work flow.`}</Text>
        </Stack>
      ) : (
        <ReadingsStep
          workOrderId={workOrderId}
          rows={query.data}
          busy={busy}
          runCommand={runCommand}
        />
      )}
    </Card>
  );
}
