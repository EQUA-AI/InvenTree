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
import { useCallback, useMemo, useState } from 'react';
import DictateCloseoutModal from './DictateCloseoutModal';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';

import { useApi } from '../../../contexts/ApiContext';
import { showApiErrorMessage } from '../../../functions/notifications';

/** Work-order shape consumed by the wizard (canonical API). */
export interface CloseoutWorkOrder {
  id: number;
  reference: string;
  title: string;
  lifecycle_status: string;
  lifecycle_version: number;
}

interface CloseoutCapture {
  id: number;
  status: string;
  source_type: string;
  revision: number | null;
  narrative: string;
}

interface ProposalField {
  value: string | number | null;
  spans: [number, number][];
  confidence: number;
  warnings: string[];
}

interface CloseoutProposal {
  id: number;
  extractor: string;
  fields: Record<string, ProposalField>;
  part_candidates: { text: string }[];
  warnings: string[];
  decisions: {
    field_path: string;
    decision: string;
    final_value: unknown;
  }[];
}

interface PartUsageRow {
  id: number;
  source: string;
  state: string;
  candidate_text: string;
  planned_quantity: string | null;
  issued_quantity: string | null;
  used_quantity: string | null;
  disposition: string;
  version: number;
}

interface ReadingRow {
  id: number;
  label: string;
  raw_text: string;
  value: string | null;
  unit: string;
  required: boolean;
  verification_state: string;
  warnings: string[];
}

interface ReadinessBlocker {
  code: string;
  message: string;
  blocking: boolean;
}

interface Readiness {
  ready: boolean;
  blockers: ReadinessBlocker[];
  warnings: ReadinessBlocker[];
}

interface EffectRow {
  id: number;
  effect_type: string;
  status: string;
  attempts: number;
  result_reference: string;
}

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
 * Closeout wizard panel (Feature #15, CO1 surface).
 *
 * The wizard never computes eligibility itself: the Preview step renders the
 * server readiness contract, and Complete drives the one existing completion
 * command with the reviewed capture id. Manual entry works with AI disabled.
 */
export function CloseoutPanel({
  workOrder
}: Readonly<{ workOrder: CloseoutWorkOrder }>) {
  const api = useApi();
  const queryClient = useQueryClient();
  const [step, setStep] = useState(0);
  const [narrative, setNarrative] = useState('');
  const [dictateOpen, setDictateOpen] = useState(false);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});
  const [downtime, setDowntime] = useState<number | ''>('');
  const [followUp, setFollowUp] = useState('');
  const [followUpRequired, setFollowUpRequired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<Record<string, any> | null>(null);

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

  const effectsQuery = useQuery({
    queryKey: ['closeout-effects', woId],
    enabled: receipt != null,
    queryFn: async () => {
      const response = await api.get<EffectRow[]>(
        apiUrl(ApiEndpoints.work_order_closeout_effects, woId)
      );
      return response.data;
    }
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

  const runCommand = useCallback(
    async (fn: () => Promise<void>) => {
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
    [invalidate]
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

  const proposalValue = useCallback(
    (field: string): string => {
      const proposed = proposalQuery.data?.fields?.[field]?.value;
      return proposed == null ? '' : String(proposed);
    },
    [proposalQuery.data]
  );

  const effectiveFieldValue = useCallback(
    (field: string): string => fieldValues[field] ?? proposalValue(field),
    [fieldValues, proposalValue]
  );

  const submitDecisions = () =>
    runCommand(async () => {
      if (!capture) return;
      const decisions = ['cause', ...REQUIRED_FIELDS]
        .map((field) => ({
          field_path: field,
          decision: 'edited',
          final_value: effectiveFieldValue(field)
        }))
        .filter((entry) => String(entry.final_value).trim().length > 0);
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

  const complete = () =>
    runCommand(async () => {
      const payload: Record<string, unknown> = {
        expected_version: workOrder.lifecycle_version,
        idempotency_key: newIdempotencyKey(),
        action: effectiveFieldValue('action'),
        result: effectiveFieldValue('result'),
        verification_summary: effectiveFieldValue('verification_summary'),
        cause: effectiveFieldValue('cause'),
        follow_up: followUp,
        follow_up_required: followUpRequired
      };
      if (downtime !== '') {
        payload.downtime_minutes = downtime;
      }
      if (capture?.status === 'reviewed') {
        payload.capture_id = capture.id;
      }
      const response = await api.post(
        apiUrl(ApiEndpoints.work_order_complete, woId),
        payload
      );
      setReceipt(response.data);
      setStep(5);
      notifications.show({
        message: t`Work order completed`,
        color: 'green'
      });
    });

  const readiness = readinessQuery.data;

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
      <Stepper
        active={step}
        onStepClick={setStep}
        size='sm'
        allowNextStepsSelect
      >
        <Stepper.Step label={t`Narrative`} icon={<IconSparkles size={16} />}>
          <NarrativeStep
            capture={capture}
            narrative={narrative}
            setNarrative={setNarrative}
            onCreate={createCapture}
            onExtract={extract}
            onDictate={() => setDictateOpen(true)}
            busy={busy}
            loading={capturesQuery.isLoading}
          />
        </Stepper.Step>
        <Stepper.Step label={t`Review`} icon={<IconListCheck size={16} />}>
          <ReviewStep
            capture={capture}
            proposal={proposalQuery.data ?? null}
            effectiveFieldValue={effectiveFieldValue}
            setFieldValue={(field, value) =>
              setFieldValues((current) => ({ ...current, [field]: value }))
            }
            onSubmit={submitDecisions}
            busy={busy}
          />
        </Stepper.Step>
        <Stepper.Step label={t`Parts`}>
          <PartsStep
            rows={partUsageQuery.data ?? []}
            onRefresh={refreshPartUsage}
            onResolve={resolveRow}
            busy={busy}
          />
        </Stepper.Step>
        <Stepper.Step label={t`Readings`}>
          <ReadingsStep
            workOrderId={woId}
            rows={readingsQuery.data ?? []}
            runCommand={runCommand}
            busy={busy}
          />
        </Stepper.Step>
        <Stepper.Step label={t`Complete`}>
          <CompleteStep
            readiness={readiness ?? null}
            loading={readinessQuery.isLoading}
            capture={capture}
            downtime={downtime}
            setDowntime={setDowntime}
            followUp={followUp}
            setFollowUp={setFollowUp}
            followUpRequired={followUpRequired}
            setFollowUpRequired={setFollowUpRequired}
            onComplete={complete}
            busy={busy}
          />
        </Stepper.Step>
        <Stepper.Completed>
          <ReceiptStep receipt={receipt} effects={effectsQuery.data ?? []} />
        </Stepper.Completed>
      </Stepper>
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
  if (!capture) {
    return (
      <Alert color='gray' mt='md'>
        {t`Capture a narrative first, or enter the fields manually and complete without a capture.`}
      </Alert>
    );
  }
  const fields = ['cause', ...REQUIRED_FIELDS];
  return (
    <Stack gap='sm' mt='md'>
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
        <Button onClick={onSubmit} loading={busy}>
          {t`Record decisions`}
        </Button>
        {capture.status === 'reviewed' && (
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

function ReadingsStep({
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

function CompleteStep({
  readiness,
  loading,
  capture,
  downtime,
  setDowntime,
  followUp,
  setFollowUp,
  followUpRequired,
  setFollowUpRequired,
  onComplete,
  busy
}: Readonly<{
  readiness: Readiness | null;
  loading: boolean;
  capture: CloseoutCapture | null;
  downtime: number | '';
  setDowntime: (value: number | '') => void;
  followUp: string;
  setFollowUp: (value: string) => void;
  followUpRequired: boolean;
  setFollowUpRequired: (value: boolean) => void;
  onComplete: () => void;
  busy: boolean;
}>) {
  return (
    <Stack gap='sm' mt='md'>
      {loading ? (
        <Loader size='sm' />
      ) : readiness == null ? null : readiness.ready ? (
        <Alert color='green' icon={<IconCircleCheck size={16} />}>
          {t`Ready to complete.`}
        </Alert>
      ) : (
        <Alert color='red' icon={<IconAlertTriangle size={16} />}>
          <Stack gap={4}>
            {readiness.blockers.map((blocker) => (
              <Text size='sm' key={`${blocker.code}-${blocker.message}`}>
                <Badge size='xs' color='red' variant='light' mr={6}>
                  {blocker.code}
                </Badge>
                {blocker.message}
              </Text>
            ))}
          </Stack>
        </Alert>
      )}
      {readiness?.warnings?.length ? (
        <Alert color='yellow'>
          <Stack gap={4}>
            {readiness.warnings.map((warning) => (
              <Text size='sm' key={`${warning.code}-${warning.message}`}>
                <Badge size='xs' color='yellow' variant='light' mr={6}>
                  {warning.code}
                </Badge>
                {warning.message}
              </Text>
            ))}
          </Stack>
        </Alert>
      ) : null}
      <Group>
        <NumberInput
          label={t`Downtime (minutes)`}
          min={0}
          value={downtime}
          onChange={(value) =>
            setDowntime(typeof value === 'number' ? value : '')
          }
          size='xs'
        />
        <Checkbox
          label={t`Follow-up required`}
          checked={followUpRequired}
          onChange={(event) => setFollowUpRequired(event.currentTarget.checked)}
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
      {capture && capture.status !== 'reviewed' && (
        <Alert color='yellow'>
          {t`The capture has not finished review; completion will be blocked until it is reviewed or abandoned.`}
        </Alert>
      )}
      <Group>
        <Button
          color='green'
          onClick={onComplete}
          loading={busy}
          disabled={readiness != null && !readiness.ready}
        >
          {t`Complete work order`}
        </Button>
      </Group>
    </Stack>
  );
}

function ReceiptStep({
  receipt,
  effects
}: Readonly<{
  receipt: Record<string, any> | null;
  effects: EffectRow[];
}>) {
  if (!receipt) {
    return (
      <Text size='sm' c='dimmed' mt='md'>
        {t`No completion receipt yet.`}
      </Text>
    );
  }
  return (
    <Stack gap='sm' mt='md'>
      <Alert color='green' icon={<IconCircleCheck size={16} />}>
        {t`Completed.`} {t`Closeout id`}: {receipt.metadata?.closeout_id ?? '-'}
      </Alert>
      {effects.length > 0 && (
        <Table withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t`Effect`}</Table.Th>
              <Table.Th>{t`Status`}</Table.Th>
              <Table.Th>{t`Attempts`}</Table.Th>
              <Table.Th>{t`Result`}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {effects.map((effect) => (
              <Table.Tr key={effect.id}>
                <Table.Td>{effect.effect_type}</Table.Td>
                <Table.Td>
                  <Badge
                    variant='light'
                    color={
                      effect.status === 'succeeded'
                        ? 'green'
                        : effect.status === 'failed'
                          ? 'red'
                          : 'yellow'
                    }
                  >
                    {effect.status}
                  </Badge>
                </Table.Td>
                <Table.Td>{effect.attempts}</Table.Td>
                <Table.Td>{effect.result_reference || '-'}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
