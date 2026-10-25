import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  MultiSelect,
  NumberInput,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Textarea
} from '@mantine/core';
import { IconChecks, IconTestPipe } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useApi } from '../../../../contexts/ApiContext';
import { useRetainedCommand } from '../../../../hooks/useRetainedCommand';
import { recordedTime } from './ExecutionFacts';
import {
  normalizeExerciseReason,
  normalizeExerciseResultReason
} from './exerciseVerificationReasons';

function exerciseResultReasonMessage(reason: string): string {
  switch (normalizeExerciseResultReason(reason)) {
    case 'published_procedure_not_applied':
      return t`Apply the current independently published exercise procedure before verification can start.`;
    case 'procedure_revision_or_snapshot_invalid':
      return t`The applied procedure is outdated or its snapshot cannot be verified. Have an authorized procedure owner review the current published revision before recording results.`;
    case 'source_or_ownership_invalid':
      return t`The linked source event or work ownership cannot be verified. Reload the source and owning work order; do not assume that verification is approved.`;
    case 'unknown':
      return t`Verification remains blocked. Reload the current source and procedure records and ask an authorized maintainer to review them.`;
  }
}

function exerciseReasonMessage(reason: string): string {
  switch (normalizeExerciseReason(reason)) {
    case 'step_missing':
      return t`The required exercise step is missing. Apply the current approved exercise procedure before recording results.`;
    case 'criteria_invalid':
      return t`The recorded criteria do not match the approved exercise definition. Reload and review the current procedure before retesting.`;
    case 'current_pass_not_recorded':
      return t`No current passing result with a valid recorder and audit record is available. Record or retest this required check.`;
    case 'value_does_not_match_prepared_evidence':
      return t`The recorded result does not match the linked prepared exercise evidence. Review that evidence and record a supported result or retest.`;
    case 'required_prepared_evidence_missing':
      return t`Required job-owned prepared evidence is missing or changed. Relink the correct evidence before retesting.`;
    case 'unknown':
      return t`This check cannot be accepted. Reload the current verification record and review the linked evidence.`;
  }
}

interface ExerciseCheck {
  sequence: number;
  title: string;
  value_type: 'boolean' | 'number';
  unit: string;
  passed: boolean;
  recorded: boolean;
  value: boolean | number | string | null;
  step_key: string;
  version: number;
  recorded_by: string | null;
  recorded_at: string | null;
  evidence_ids: number[];
  suggested_evidence_ids: number[];
  prepared_note: string;
  note: string;
  reasons: string[];
}

interface ExerciseResult {
  applicable: boolean;
  work_order_id: number;
  approved: boolean;
  profile: string;
  passed_count: number;
  required_count: number;
  can_record: boolean;
  can_apply: boolean;
  source: { anomaly_id: number; status: string };
  application_id: number | null;
  published_revision_id: number | null;
  evidence: { id: number; label: string; prepared: boolean }[];
  checks: ExerciseCheck[];
  reasons: string[];
}

function ExerciseStep({
  check,
  result,
  onRecorded
}: Readonly<{
  check: ExerciseCheck;
  result: ExerciseResult;
  onRecorded: () => Promise<void>;
}>) {
  const [value, setValue] = useState<string | number>(
    check.value === null ? '' : String(check.value)
  );
  const [note, setNote] = useState(check.note);
  const [evidence, setEvidence] = useState(check.evidence_ids.map(String));
  const [baselineVersion, setBaselineVersion] = useState(check.version);
  const [pendingVersion, setPendingVersion] = useState<number | null>(null);
  useEffect(() => {
    if (pendingVersion !== null && check.version >= pendingVersion) {
      if (check.version === pendingVersion) setBaselineVersion(pendingVersion);
      setPendingVersion(null);
    }
  }, [check.version, pendingVersion]);
  const [reviewedSource, setReviewedSource] = useState(false);
  const url = `/api/tasks/work-orders/${result.work_order_id}/steps/${check.step_key}/complete/`;
  const command = useRetainedCommand(
    `exercise-step:${result.work_order_id}:${check.step_key}`,
    new RegExp(`^${url}$`)
  );
  const changedElsewhere =
    baselineVersion !== check.version && check.version !== pendingVersion;
  const numeric = check.value_type === 'number';
  const record = async () => {
    const recordedValue = numeric
      ? { number: String(value) }
      : { boolean: value === 'true' };
    const request = command.intent ?? {
      url,
      body: {
        expected_version: baselineVersion,
        idempotency_key: crypto.randomUUID(),
        value: recordedValue,
        evidence_ids: evidence.map(Number),
        note:
          check.sequence === 3
            ? `${note}\nSource event #${result.source.anomaly_id} reviewed with status ${result.source.status}; alarm reset remains separate.`
            : note
      }
    };
    const response = await command.execute<{
      step_key: string;
      application: number;
      version: number;
      value: unknown;
    }>(
      request,
      (receipt, retained) =>
        receipt.step_key === check.step_key &&
        receipt.application === result.application_id &&
        receipt.version === Number(retained.body.expected_version) + 1 &&
        JSON.stringify(receipt.value) === JSON.stringify(retained.body.value)
    );
    if (response) {
      setPendingVersion(response.version);
      setReviewedSource(false);
      await onRecorded();
    } else await onRecorded();
  };
  const disabled =
    command.busy ||
    pendingVersion !== null ||
    Boolean(command.intent) ||
    !result.can_record;
  return (
    <Paper
      withBorder
      p='md'
      radius='sm'
      data-testid={`exercise-check-${check.sequence}`}
    >
      <Stack gap='sm'>
        <Group justify='space-between' align='flex-start'>
          <Text fw={600} size='sm' style={{ flex: '1 1 180px' }}>
            {check.sequence}. {check.title}
          </Text>
          <Badge
            color={check.passed ? 'teal' : check.recorded ? 'orange' : 'gray'}
            variant='light'
            style={{ color: 'var(--mantine-color-text)' }}
          >
            {check.passed
              ? t`PASS — exercise`
              : check.recorded
                ? t`Not accepted`
                : t`Awaiting record`}
          </Badge>
        </Group>
        <Text size='xs'>
          {check.sequence === 2
            ? t`Maximum of Winding 5 and 6. Native exercise bound ≤124.9°C; warning begins at 125°C. This prepared E06 exercise additionally requires exactly 96°C to match W5=94°C and W6=96°C. These are not historian data or OEM release limits.`
            : check.sequence === 1
              ? t`Prepared cooler-restoration record only. No field work or safeguards are certified here.`
              : t`Prepared 15-minute stable trial only. Review the actual source alarm separately; no physical operating permission.`}
        </Text>
        {check.reasons.length > 0 && (
          <Alert
            color='orange'
            styles={{ message: { color: 'var(--mantine-color-text)' } }}
          >
            {check.reasons.map((reason) => (
              <Text size='sm' key={reason}>
                {exerciseReasonMessage(reason)}
              </Text>
            ))}
          </Alert>
        )}
        {numeric ? (
          <NumberInput
            label={t`Maximum winding temperature (°C)`}
            value={value}
            onChange={setValue}
            decimalScale={1}
            step={0.1}
            disabled={disabled}
          />
        ) : (
          <Select
            label={t`${check.title} — prepared result`}
            value={String(value) || null}
            onChange={(next) => setValue(next ?? '')}
            data={[
              { value: 'true', label: t`Yes` },
              { value: 'false', label: t`No` }
            ]}
            disabled={disabled}
          />
        )}
        <MultiSelect
          label={t`Linked prepared evidence`}
          placeholder={t`Select required evidence sheets`}
          data={result.evidence.map((row) => ({
            value: String(row.id),
            label: row.label
          }))}
          value={evidence}
          onChange={setEvidence}
          disabled={disabled}
        />
        <Textarea
          label={t`Recorded exercise note`}
          value={note}
          onChange={(event) => setNote(event.currentTarget.value)}
          autosize
          minRows={2}
          maxRows={6}
          disabled={disabled}
        />
        <Button
          variant='subtle'
          size='xs'
          disabled={disabled}
          onClick={() => {
            setEvidence(check.suggested_evidence_ids.map(String));
            setNote(check.prepared_note);
          }}
        >{t`Use linked exercise evidence and note`}</Button>
        {check.sequence === 3 && (
          <Checkbox
            checked={reviewedSource}
            onChange={(event) => setReviewedSource(event.currentTarget.checked)}
            label={t`I reviewed source event #${result.source.anomaly_id}: ${result.source.status}. Reset is separate.`}
            disabled={disabled}
          />
        )}
        {changedElsewhere && (
          <Alert color='orange'>
            <Stack gap='xs'>
              <Text size='sm'>{t`This result changed. Review the latest record before replacing it.`}</Text>
              <Button
                variant='default'
                size='xs'
                onClick={() => {
                  setPendingVersion(null);
                  setBaselineVersion(check.version);
                  setValue(check.value === null ? '' : String(check.value));
                  setNote(check.note);
                  setEvidence(check.evidence_ids.map(String));
                }}
              >{t`Load current recorded result`}</Button>
            </Stack>
          </Alert>
        )}
        {command.error && (
          <Alert color='red' role='alert'>
            {command.error}
          </Alert>
        )}
        {check.recorded_at && (
          <Text size='xs'>
            {check.recorded_by ?? t`Recorder unavailable`} ·{' '}
            {recordedTime(check.recorded_at)} · {t`Result version`}{' '}
            {check.version}
          </Text>
        )}
        {result.can_record && (
          <Button
            loading={command.busy}
            variant='light'
            styles={{ label: { color: 'var(--mantine-color-text)' } }}
            disabled={
              command.storageFailed ||
              (!command.intent &&
                (changedElsewhere ||
                  value === '' ||
                  evidence.length === 0 ||
                  !note.trim() ||
                  (check.sequence === 3 && !reviewedSource)))
            }
            onClick={() => void record()}
          >
            {command.intent
              ? t`Retry same result`
              : check.recorded
                ? t`Record exercise retest`
                : t`Record exercise result`}
          </Button>
        )}
      </Stack>
    </Paper>
  );
}

export function ExerciseVerificationPanel({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const api = useApi();
  const queryClient = useQueryClient();
  const base = `/api/tasks/work-orders/${workOrder.id}/`;
  const query = useQuery<ExerciseResult>({
    queryKey: ['exercise-verification', workOrder.id],
    queryFn: async () => (await api.get(`${base}exercise-verification/`)).data,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: 'always'
  });
  const command = useRetainedCommand(
    `exercise-setup:${workOrder.id}`,
    new RegExp(`^${base}(exercise-verification/evidence|procedures/apply)/$`)
  );
  const [evidenceConfirmed, setEvidenceConfirmed] = useState(false);
  const refresh = async () => {
    await Promise.all([
      query.refetch(),
      queryClient.invalidateQueries({ queryKey: ['work-order', workOrder.id] }),
      queryClient.invalidateQueries({
        queryKey: ['work-order-readiness', workOrder.id]
      })
    ]);
  };
  const prepare = async (kind: 'evidence' | 'procedure') => {
    if (!query.data) return;
    const evidence = kind === 'evidence';
    const request = command.intent ?? {
      url: `${base}${evidence ? 'exercise-verification/evidence' : 'procedures/apply'}/`,
      body: {
        idempotency_key: crypto.randomUUID(),
        ...(evidence
          ? {}
          : {
              expected_version: workOrder.lifecycle_version,
              revision_id: query.data.published_revision_id
            })
      }
    };
    const response = await command.execute<Record<string, unknown>>(
      request,
      (receipt, retained) =>
        retained.url.includes('/evidence/')
          ? receipt.work_order_id === workOrder.id &&
            receipt.idempotency_key === retained.body.idempotency_key &&
            receipt.applicable === true
          : receipt.work_order === workOrder.id &&
            receipt.revision === retained.body.revision_id &&
            receipt.idempotency_key === retained.body.idempotency_key
    );
    if (response) await refresh();
  };
  if (query.isPending)
    return (
      <Group aria-live='polite'>
        <Loader size='xs' />
        <Text size='sm'>{t`Reading exercise verification…`}</Text>
      </Group>
    );
  if (query.isError || !query.data)
    return (
      <Alert color='orange' title={t`Exercise verification unavailable`}>
        <Stack gap='xs'>
          <Text size='sm'>{t`No exercise approval can be established. Reload the current source and procedure records.`}</Text>
          <Button
            variant='default'
            size='xs'
            onClick={() => void query.refetch()}
          >{t`Reload verification`}</Button>
        </Stack>
      </Alert>
    );
  const result = query.data;
  if (!result.applicable) return null;
  return (
    <Paper
      withBorder
      p='md'
      radius='sm'
      data-testid='exercise-verification-panel'
    >
      <Stack gap='md'>
        <Group justify='space-between'>
          <Group gap='xs'>
            <IconTestPipe size={20} />
            <Text fw={600}>{t`Pump 08 return verification`}</Text>
          </Group>
          <Badge variant='outline' color='blue'>{t`EXERCISE ONLY`}</Badge>
        </Group>
        <Alert
          color={result.approved ? 'teal' : 'orange'}
          icon={result.approved ? <IconChecks size={20} /> : undefined}
          title={
            result.approved
              ? t`RETURN VERIFICATION PASSED`
              : t`RETURN VERIFICATION NOT APPROVED`
          }
          aria-live='polite'
          styles={{
            message: { color: 'var(--mantine-color-text)' },
            title: { color: 'var(--mantine-color-text)' }
          }}
        >
          <Stack gap={4}>
            <Text
              fw={600}
              size='sm'
            >{t`Pump 08 | ${result.passed_count} of ${result.required_count} checks accepted`}</Text>
            {result.approved && (
              <Stack gap={2}>
                <Text size='sm'>{t`Cooler restored: PASS`}</Text>
                <Text size='sm'>{t`Maximum winding temperature: ${Number(result.checks.find((check) => check.sequence === 2)?.value).toFixed(1)}°C / exercise limit <125.0°C: PASS`}</Text>
                <Text size='sm'>{t`Controlled trial: PASS`}</Text>
              </Stack>
            )}
            <Text size='sm'>
              {result.approved
                ? t`Exercise verification approved. Ready for permitted work-order closeout.`
                : t`One or more required exercise checks failed or lack valid evidence. The exercise scenario remains held for correction/retest. Work-order closeout is not automatically authorized.`}
            </Text>
            <Text size='xs'>{t`Source: prepared exercise measurements. Physical operating release is not issued. Native closeout and all independent safety, assignment and approval gates remain separate.`}</Text>
          </Stack>
        </Alert>
        {!result.application_id && (
          <Alert color='blue' title={t`Governed procedure required`}>
            <Stack gap='xs'>
              <Text size='sm'>
                {result.published_revision_id
                  ? t`Apply the current published Pump 08 exercise revision. This does not record or pass any check.`
                  : t`An authorized author and independent reviewer must publish P08-MOTOR-COOL-RTS-EX for this plant client before verification can start.`}
              </Text>
              {result.can_apply && result.published_revision_id && (
                <Button
                  size='xs'
                  variant='light'
                  loading={command.busy}
                  disabled={Boolean(command.intent)}
                  onClick={() => void prepare('procedure')}
                >{t`Apply published exercise procedure`}</Button>
              )}
            </Stack>
          </Alert>
        )}
        {result.evidence.length === 0 && result.can_record && (
          <Paper withBorder p='sm'>
            <Stack gap='sm'>
              <Checkbox
                checked={evidenceConfirmed}
                onChange={(event) =>
                  setEvidenceConfirmed(event.currentTarget.checked)
                }
                disabled={command.busy}
                label={t`Attach the prepared E03/E05/E06 exercise sheets. These do not represent physical work or witnessed tests.`}
              />
              <Button
                variant='default'
                size='xs'
                disabled={!evidenceConfirmed || Boolean(command.intent)}
                loading={command.busy}
                onClick={() => void prepare('evidence')}
              >{t`Attach prepared exercise evidence`}</Button>
            </Stack>
          </Paper>
        )}
        {command.intent && (
          <Button
            variant='default'
            loading={command.busy}
            onClick={() =>
              void prepare(
                command.intent!.url.includes('/evidence/')
                  ? 'evidence'
                  : 'procedure'
              )
            }
          >{t`Retry same setup command`}</Button>
        )}
        {command.error && (
          <Alert color='red' role='alert'>
            {command.error}
          </Alert>
        )}
        {result.checks.length > 0 && (
          <SimpleGrid cols={{ base: 1, lg: 3 }} spacing='md'>
            {result.checks.map((check) => (
              <ExerciseStep
                key={check.step_key}
                check={check}
                result={result}
                onRecorded={refresh}
              />
            ))}
          </SimpleGrid>
        )}
        {result.reasons.length > 0 && (
          <Stack gap={4} data-testid='exercise-governance-blockers'>
            <Text size='sm' fw={600}>{t`Verification blockers`}</Text>
            {result.reasons.map((reason) => (
              <Text size='sm' key={reason}>
                {exerciseResultReasonMessage(reason)}
              </Text>
            ))}
          </Stack>
        )}
        <Text size='xs'>{t`This software-only outcome does not grant technical acceptance, operating permission, observed return or service state, and never completes or closes work.`}</Text>
      </Stack>
    </Paper>
  );
}
