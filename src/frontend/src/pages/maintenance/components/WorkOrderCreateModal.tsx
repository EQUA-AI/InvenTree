import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  CloseButton,
  Group,
  Loader,
  Modal,
  NumberInput,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea,
  useMantineTheme
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { useDebouncedValue, useMediaQuery } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';

import type { StaffMember } from '@lib/types/Staff';
import { TradeRequirementsFields } from '../../../components/staff/TradeRequirementsFields';
import {
  assignmentReview,
  personnelPlanning
} from '../../../components/staff/staffAssignment';
import { useStaffAccess } from '../../../components/staff/useStaffDirectory';
import { useApi } from '../../../contexts/ApiContext';
import { showApiErrorMessage } from '../../../functions/notifications';
import { useRetainedCommand } from '../../../hooks/useRetainedCommand';
import type { RetainedCommand } from '../../../hooks/useRetainedCommand';
import { type IntakeDraft, WorkIntakePanel } from './WorkIntakePanel';
import { WorkOrderAssigneeSelect } from './WorkOrderAssigneeSelect';
import { type DuplicateRepair, duplicateSummary } from './duplicateSummary';
import { matchesWorkPackageReceipt } from './workPackageReceipt';
export type { DuplicateRepair } from './duplicateSummary';
import {
  type AssignmentWarningCode,
  assignmentWarningCode,
  assignmentWarningNeedsReview
} from './workPackageWarnings';

/** One requested part line in the draft. */
interface DraftPart {
  partId: number;
  partName: string;
  quantity: number;
}

export interface WorkPackageResult {
  work_order_id: number;
  work_order_reference: string;
  repair_packet_id: number | null;
  repair_packet_reference: string;
  replayed: boolean;
  warnings: string[];
  machine_id: number;
  anomaly_id: number | null;
  assigned_to_id: number | null;
  idempotency_key: string;
  lifecycle_version: number;
}

export function showWorkPackageWarnings(warnings: readonly string[] = []) {
  if (!warnings.length) return;
  const messages: Record<AssignmentWarningCode, string> = {
    ASSIGNMENT_UNASSIGNED: t`No owner is assigned. Assign authorized staff before work starts.`,
    ASSIGNMENT_QUALIFIED: t`The assigned staff member matches the recorded trade requirements. This is not a safety certification.`,
    ASSIGNMENT_REQUIREMENTS_UNKNOWN: t`Trade requirements have not been confirmed. Review staff suitability before work starts.`,
    ASSIGNMENT_MANUAL_OVERRIDE: t`Authorized trade mismatch override recorded with its reason. This does not authorize work to start.`,
    ASSIGNMENT_PERSON_UNAVAILABLE: t`The selected staff member is unavailable. Review the owner before work starts.`,
    ASSIGNMENT_REQUIREMENTS_INVALID: t`The recorded trade requirements are invalid. Review and confirm them before work starts.`,
    ASSIGNMENT_TRADE_MISMATCH: t`The selected staff member does not cover every required trade. Review the assignment or obtain an authorized override.`,
    ASSIGNMENT_AMBIGUOUS: t`Several staff members match the recorded requirements. Review and select an owner.`,
    ASSIGNMENT_NO_QUALIFIED_MATCH: t`No staff member matches all recorded trade requirements. Review the requirements and assignment.`,
    ASSIGNMENT_REVIEW_REQUIRED: t`The assignment needs human review before work starts.`
  };
  const codes = warnings.map(assignmentWarningCode);
  const needsReview = codes.some(assignmentWarningNeedsReview);
  notifications.show({
    title: needsReview
      ? t`Work order created — review required`
      : t`Work order created — assignment recorded`,
    message: warnings
      .map((warning, index) => {
        const code = codes[index];
        return code ? messages[code] : warning;
      })
      .join('\n'),
    color: needsReview ? 'orange' : 'blue',
    styles: { description: { color: 'var(--mantine-color-text)' } },
    closeButtonProps: { 'aria-label': t`Close` },
    autoClose: false
  });
}

export interface WorkOrderCreateModalProps {
  opened: boolean;
  onClose: () => void;
  /** Fixes the modal to one machine (machine page, anomaly intake). */
  machineId?: number;
  /** Where this draft came from; recorded as work-package provenance. */
  origin?: 'manual' | 'anomaly' | 'chat';
  /**
   * Health anomaly this repair answers. The server links it to the created work
   * order and packet and freezes its signals as evidence, so the decision stays
   * reconstructable after the live values move on.
   */
  anomalyId?: number;
  /** Anomaly-derived starting points. The user may amend any of them; editing
   * the text never mutates the underlying evidence snapshot. */
  initialTitle?: string;
  initialFaultSummary?: string;
  initialCriticality?: string;
  autoDraft?: boolean;
  onCreated?: (result: WorkPackageResult) => void;
}

/**
 * Idempotency key for one create attempt.
 *
 * `crypto.randomUUID` is unavailable on insecure origins, so fall back to a
 * random string rather than throwing: uniqueness is all the server needs.
 */
function newIdempotencyKey(): string {
  if (typeof crypto?.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `wp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

const WORK_ORDER_TYPES = [
  { value: 'corrective', label: 'Corrective' },
  { value: 'preventive', label: 'Preventive' },
  { value: 'inspection', label: 'Inspection' },
  { value: 'calibration', label: 'Calibration' },
  { value: 'other', label: 'Other' }
];

interface FormValues {
  machine: string | null;
  title: string;
  workOrderType: string;
  priority: string;
  faultSummary: string;
  symptom: string;
  productionImpact: string;
  criticality: string;
  assignee: string;
  assignedTo: string | null;
  requiredTrades: string[];
  tradeRequirementsConfirmed: boolean;
  assignmentOverrideReason: string;
  assignmentOverrideConfirmed: boolean;
  dueDate: Date | null;
  estimatedMinutes: number | string;
  createPacket: boolean;
}

/**
 * Field rules, defined once. Mantine form rebuilds `setFieldValue` whenever its
 * rules object changes identity, so an inline object here made the packet
 * toggle's effect below re-run on every render and loop ("Maximum update depth
 * exceeded") wherever this modal is mounted - the machine header and Health
 * tab included - even while it stays closed.
 */
const VALIDATION = {
  title: (value: string) =>
    value.trim().length === 0 ? t`Describe the work in a short title.` : null,
  // Every work order is anchored to a machine; the backend enforces this too.
  machine: (value: string | null) =>
    value ? null : t`Select the machine for this work.`
};

/**
 * Reusable "New work order" experience for the Maintenance workspace.
 *
 * Submits one versioned work-package draft to the audited compound command
 * (`/api/maintenance/work-packages/create/`), which atomically creates the
 * machine-linked work order, its optional Repair Packet, required-part lines
 * and resolved safety gates. It never POSTs a raw Kanban card, and it never
 * starts the work: starting a repair is a separate readiness-gated transition.
 *
 * The same modal serves the manual, machine and anomaly entry points; pass
 * `machineId` to fix the asset and `origin` to record where the draft came from.
 */
export function WorkOrderCreateModal({
  opened,
  onClose,
  machineId,
  origin = 'manual',
  anomalyId,
  initialTitle,
  initialFaultSummary,
  initialCriticality,
  autoDraft = false,
  onCreated
}: Readonly<WorkOrderCreateModalProps>) {
  const api = useApi();
  const theme = useMantineTheme();
  const staffAccess = useStaffAccess();
  const [selectedStaff, setSelectedStaff] = useState<StaffMember>();
  const [serverAssignmentMismatch, setServerAssignmentMismatch] =
    useState(false);

  // Below Mantine's `sm` breakpoint a size='lg' modal is unusable; go
  // full-screen so the form stays reachable on a phone.
  const isSmallScreen = useMediaQuery('(max-width: 48em)');

  const [saving, setSaving] = useState(false);
  const [sourceReady, setSourceReady] = useState(false);
  const command = useRetainedCommand(
    `work-package:${machineId ?? 'manual'}:${anomalyId ?? 'manual'}`,
    /^\/api\/maintenance\/work-packages\/create\/$/
  );
  const [draftToken, setDraftToken] = useState('');
  const [reviewSource, setReviewSource] = useState<'ai' | 'prepared' | null>(
    null
  );
  const [reviewed, setReviewed] = useState(false);
  const [suggestedOwner, setSuggestedOwner] = useState<string | null>(null);
  const [pendingDraft, setPendingDraft] = useState<IntakeDraft | null>(null);
  const [draftModel, setDraftModel] = useState<string | null>(null);
  // Non-null once the server has reported existing open repairs; submitting
  // again then carries an explicit override plus the reason typed below.
  const [duplicates, setDuplicates] = useState<DuplicateRepair[] | null>(null);
  const [overrideReason, setOverrideReason] = useState('');
  const [parts, setParts] = useState<DraftPart[]>([]);
  const [partSearch, setPartSearch] = useState('');
  const [debouncedPartSearch] = useDebouncedValue(partSearch, 300);
  const [partResults, setPartResults] = useState<
    { pk: number; name: string; IPN: string }[]
  >([]);
  const [partSearchLoading, setPartSearchLoading] = useState(false);

  // A fresh key per attempt makes retry-after-failure safe: the server treats a
  // repeat of the same key as a replay and returns the original work order
  // rather than creating a second one.
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);

  const form = useForm<FormValues>({
    initialValues: {
      machine: machineId ? String(machineId) : null,
      title: initialTitle ?? '',
      workOrderType: 'corrective',
      priority: initialCriticality === 'critical' ? 'high' : 'medium',
      faultSummary: initialFaultSummary ?? '',
      symptom: '',
      productionImpact: '',
      criticality: initialCriticality ?? 'medium',
      assignee: '',
      assignedTo: null,
      requiredTrades: [],
      tradeRequirementsConfirmed: false,
      assignmentOverrideReason: '',
      assignmentOverrideConfirmed: false,
      dueDate: null,
      estimatedMinutes: '',
      createPacket: true
    },
    validate: VALIDATION
  });
  const assignmentMismatch =
    serverAssignmentMismatch ||
    assignmentReview(
      selectedStaff,
      form.values.requiredTrades,
      form.values.tradeRequirementsConfirmed
    ) === 'mismatch';
  const assignmentBlocked =
    form.values.assignedTo != null &&
    (!staffAccess.directory ||
      !staffAccess.assign ||
      !selectedStaff ||
      (assignmentMismatch &&
        !(
          staffAccess.override &&
          form.values.assignmentOverrideConfirmed &&
          form.values.assignmentOverrideReason.trim()
        )));
  const assignmentReviewKey = JSON.stringify([
    form.values.assignedTo,
    form.values.requiredTrades,
    form.values.tradeRequirementsConfirmed
  ]);
  useEffect(() => {
    setServerAssignmentMismatch(false);
    form.setFieldValue('assignmentOverrideConfirmed', false);
    form.setFieldValue('assignmentOverrideReason', '');
  }, [assignmentReviewKey]);
  const selectedMachine = form.values.machine;
  useEffect(() => {
    form.setFieldValue('assignedTo', null);
    form.setFieldValue('tradeRequirementsConfirmed', false);
    setSelectedStaff(undefined);
  }, [selectedMachine]);

  const machinesQuery = useQuery<
    { pk: number; name: string; location: string }[],
    Error
  >({
    queryKey: ['asset-machines'],
    enabled: opened && !machineId,
    queryFn: async () => {
      const response = await api.get(apiUrl(ApiEndpoints.asset_machine_list));
      return response.data ?? [];
    }
  });

  const machineOptions = useMemo(
    () =>
      (machinesQuery.data ?? []).map((machine) => ({
        value: String(machine.pk),
        label: machine.location
          ? `${machine.name} — ${machine.location}`
          : machine.name
      })),
    [machinesQuery.data]
  );

  // The modal stays mounted while `opened` toggles, so a second anomaly would
  // otherwise inherit the first one's prefilled text. Re-seed on each open.
  const setFormValues = form.setValues;
  const formRef = useRef(form);
  formRef.current = form;
  const resetFormDirty = form.resetDirty;
  useEffect(() => {
    if (!opened) {
      return;
    }
    setFormValues((current) => ({
      ...current,
      machine: machineId ? String(machineId) : current.machine,
      title: initialTitle ?? '',
      faultSummary: initialFaultSummary ?? '',
      criticality: initialCriticality ?? 'medium',
      priority: initialCriticality === 'critical' ? 'high' : 'medium'
    }));
    setDraftToken('');
    setReviewSource(null);
    setReviewed(false);
    setSuggestedOwner(null);
    setPendingDraft(null);
    setDraftModel(null);
    resetFormDirty();
  }, [
    opened,
    machineId,
    initialTitle,
    initialFaultSummary,
    initialCriticality,
    setFormValues,
    resetFormDirty
  ]);

  // Keep the packet toggle aligned with the work-order type until the user
  // overrides it: corrective work needs a fault-to-fix aggregate, planning and
  // administrative work does not. Once overridden, the choice is theirs and the
  // type no longer moves it.
  const [packetChoiceTouched, setPacketChoiceTouched] = useState(false);
  const workOrderType = form.values.workOrderType;
  const setFieldValue = form.setFieldValue;
  const setAssignedTo = useCallback(
    (value: string | null) => setFieldValue('assignedTo', value),
    [setFieldValue]
  );
  const applyDraft = useCallback(
    (draft: IntakeDraft) => {
      setFormValues((current) => ({
        ...current,
        title: draft.fields.title,
        faultSummary: draft.fields.work_scope,
        symptom: draft.fields.symptom,
        productionImpact: draft.fields.production_impact,
        tradeRequirementsConfirmed: false,
        assignmentOverrideReason: '',
        assignmentOverrideConfirmed: false,
        ...(draft.prepared
          ? {
              workOrderType: draft.prepared.work_order_type,
              priority: draft.prepared.priority,
              criticality: draft.prepared.criticality,
              createPacket: true,
              dueDate: null,
              estimatedMinutes: '',
              assignedTo: null,
              requiredTrades: []
            }
          : {})
      }));
      if (draft.prepared) {
        setParts([]);
        setSuggestedOwner(draft.prepared.suggested_owner_username);
        setPacketChoiceTouched(true);
      }
      setDraftToken(draft.draft_token ?? '');
      setDraftModel(
        draft.provider
          ? `${draft.provider.model} · ${draft.provider.run_id}`
          : null
      );
      setReviewSource(draft.prepared ? 'prepared' : 'ai');
      setReviewed(false);
      setPendingDraft(null);
    },
    [setFormValues]
  );
  const offerDraft = useCallback(
    (draft: IntakeDraft) => {
      if (formRef.current.isDirty()) setPendingDraft(draft);
      else applyDraft(draft);
    },
    [applyDraft]
  );
  useEffect(() => {
    const refusal = command.refusal;
    if (
      refusal?.data.code === 'DUPLICATE_OPEN_REPAIR' &&
      Array.isArray(refusal.data.duplicates)
    ) {
      setDuplicates(refusal.data.duplicates as DuplicateRepair[]);
    }
  }, [command.refusal]);
  useEffect(() => {
    if (!packetChoiceTouched) {
      setFieldValue('createPacket', workOrderType === 'corrective');
    }
  }, [workOrderType, packetChoiceTouched, setFieldValue]);

  useEffect(() => {
    if (!debouncedPartSearch || debouncedPartSearch.length < 2) {
      setPartResults([]);
      return;
    }

    let cancelled = false;
    setPartSearchLoading(true);

    api
      .get(apiUrl(ApiEndpoints.part_list), {
        params: { search: debouncedPartSearch, limit: 20 }
      })
      .then((response) => {
        if (!cancelled) {
          setPartResults(response.data?.results ?? response.data ?? []);
        }
      })
      .catch(() => {
        if (!cancelled) setPartResults([]);
      })
      .finally(() => {
        if (!cancelled) setPartSearchLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [debouncedPartSearch, api]);

  const addPart = useCallback(
    (partId: number, partName: string) => {
      if (parts.some((part) => part.partId === partId)) return;
      setParts((current) => [...current, { partId, partName, quantity: 1 }]);
      setPartSearch('');
      setPartResults([]);
    },
    [parts]
  );

  const handleClose = useCallback(() => {
    if (command.busy) return;
    form.reset();
    setSelectedStaff(undefined);
    setParts([]);
    setPartSearch('');
    setPartResults([]);
    setDuplicates(null);
    setOverrideReason('');
    setSaving(false);
    onClose();
    // form is stable for the lifetime of the modal
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose, command.busy]);

  const sendCreation = async (request: RetainedCommand) => {
    const result = await command.execute<WorkPackageResult>(
      request,
      matchesWorkPackageReceipt
    );
    if (result) {
      showWorkPackageWarnings(result.warnings);
      setIdempotencyKey(newIdempotencyKey());
      onCreated?.(result);
      form.reset();
      setParts([]);
      setDuplicates(null);
      setOverrideReason('');
      onClose();
    }
  };

  const handleSubmit = form.onSubmit(async (values) => {
    if (machineId && anomalyId && !sourceReady) return;
    if (
      reviewSource &&
      (!reviewed || (values.createPacket && !values.assignedTo))
    ) {
      if (!values.assignedTo)
        form.setFieldError(
          'assignedTo',
          t`Select authorized staff before creating this repair packet.`
        );
      return;
    }
    if (assignmentBlocked) return;
    setSaving(true);

    const draft = {
      schema_version: 1,
      idempotency_key: idempotencyKey,
      origin,
      machine_id: Number(values.machine),
      title: values.title.trim(),
      description: values.faultSummary.trim(),
      work_order_type: values.workOrderType,
      priority: values.priority,
      create_repair_packet: values.createPacket,
      fault: {
        summary: values.faultSummary.trim(),
        symptom: values.symptom.trim(),
        production_impact: values.productionImpact.trim(),
        criticality: values.criticality
      },
      parts: parts.map((part) => ({
        part_id: part.partId,
        quantity: part.quantity
      })),
      source: anomalyId
        ? { anomaly_id: anomalyId, ai_draft_token: draftToken }
        : {},
      duplicate_override: duplicates !== null,
      duplicate_override_reason: overrideReason.trim(),
      planning: {
        ...personnelPlanning({
          canAssign: staffAccess.directory && staffAccess.assign,
          canPlan: staffAccess.directory && staffAccess.plan,
          assignedTo: values.assignedTo ? Number(values.assignedTo) : null,
          ownerKey: 'assigned_to_id',
          requiredTrades: values.requiredTrades,
          confirmed: values.tradeRequirementsConfirmed,
          legacyAssignee: values.assignee,
          ...(assignmentMismatch && values.assignmentOverrideConfirmed
            ? { overrideReason: values.assignmentOverrideReason }
            : {})
        }),
        due_date: values.dueDate
          ? dayjs(values.dueDate).format('YYYY-MM-DD')
          : null,
        estimated_minutes:
          typeof values.estimatedMinutes === 'number'
            ? values.estimatedMinutes
            : null
      }
    };

    try {
      await sendCreation({
        url: apiUrl(ApiEndpoints.maintenance_work_package_create),
        body: draft
      });
    } catch (error: any) {
      if (error?.response?.data?.code === 'ASSIGNMENT_TRADE_MISMATCH') {
        setServerAssignmentMismatch(true);
      }
      // 409 is not a malformed request: this machine already has open repair
      // work. Show what exists and make proceeding a deliberate, stated choice
      // rather than a retry the user does not realise is a second work order.
      if (
        error?.response?.status === 409 &&
        Array.isArray(error.response.data?.duplicates)
      ) {
        setDuplicates(error.response.data?.duplicates ?? []);
        setSaving(false);
        return;
      }
      showApiErrorMessage({
        error,
        title: t`Could not create the work order`
      });
      setSaving(false);
    }
    setSaving(false);
  });

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title={t`New work order`}
      size='lg'
      fullScreen={isSmallScreen}
      closeOnEscape={!command.busy}
      closeOnClickOutside={!command.busy}
      closeButtonProps={{ disabled: command.busy, 'aria-label': t`Close` }}
    >
      {command.intent && (
        <Alert
          color='orange'
          title={t`Creation outcome not yet confirmed`}
          mb='md'
        >
          <Stack gap='xs'>
            <Text size='sm'>{t`The exact request is retained. Retry it to retrieve the original result; do not submit a replacement.`}</Text>
            <Button
              variant='default'
              loading={command.busy}
              disabled={command.storageFailed}
              onClick={() => void sendCreation(command.intent!)}
            >{t`Retry same creation`}</Button>
          </Stack>
        </Alert>
      )}
      {command.error &&
        command.refusal?.data.code !== 'DUPLICATE_OPEN_REPAIR' && (
          <Alert color='red' role='alert' mb='md'>
            {command.error}
          </Alert>
        )}
      {command.refusal?.data.code === 'IDEMPOTENCY_CONFLICT' && (
        <Button
          variant='default'
          mb='md'
          onClick={() => setIdempotencyKey(newIdempotencyKey())}
        >{t`Start a new reviewed request`}</Button>
      )}
      <form onSubmit={handleSubmit}>
        <fieldset
          disabled={command.busy || Boolean(command.intent)}
          style={{ border: 0, margin: 0, padding: 0, minWidth: 0 }}
        >
          <Stack gap='md'>
            {opened && machineId && anomalyId && (
              <WorkIntakePanel
                key={`${machineId}:${anomalyId}`}
                machineId={machineId}
                anomalyId={anomalyId}
                disabled={Boolean(command.intent) || command.busy}
                autoDraft={autoDraft}
                onReadyChange={setSourceReady}
                onApply={offerDraft}
              />
            )}
            {pendingDraft && (
              <Alert
                color='blue'
                title={t`Draft ready — your edits are preserved`}
              >
                <Stack gap='xs'>
                  <Text size='sm'>{t`Applying this draft replaces the scope fields. A prepared specification also clears parts and unverified planning dates.`}</Text>
                  <Button
                    size='xs'
                    variant='light'
                    onClick={() => applyDraft(pendingDraft)}
                  >{t`Apply draft to form`}</Button>
                </Stack>
              </Alert>
            )}
            {reviewSource && (
              <Alert
                color={reviewSource === 'ai' ? 'blue' : 'gray'}
                title={
                  reviewSource === 'ai'
                    ? t`AI-assisted draft — review before creating`
                    : t`Prepared exercise specification — not AI output`
                }
              >
                <Text size='sm'>{t`Nothing is created until you review this form and choose Create work order. Exercise findings are not physical maintenance or operating permission.`}</Text>
                {draftModel && (
                  <Text size='xs' style={{ overflowWrap: 'anywhere' }}>
                    {t`Model / generation receipt`}: {draftModel}
                  </Text>
                )}
              </Alert>
            )}
            {machineId ? null : (
              <Select
                label={t`Machine`}
                placeholder={
                  machinesQuery.isLoading
                    ? t`Loading machines…`
                    : t`Select the machine this work is for`
                }
                data={machineOptions}
                searchable
                withAsterisk
                nothingFoundMessage={t`No machines found`}
                disabled={machinesQuery.isLoading}
                {...form.getInputProps('machine')}
              />
            )}

            <TextInput
              label={t`Title`}
              placeholder={t`Summarize the work in one line`}
              withAsterisk
              {...form.getInputProps('title')}
            />

            <SimpleGrid cols={{ base: 1, sm: 3 }} spacing='md'>
              <Select
                label={t`Work order type`}
                data={WORK_ORDER_TYPES}
                {...form.getInputProps('workOrderType')}
              />
              <Select
                label={t`Priority`}
                data={[
                  { value: 'low', label: t`Low` },
                  { value: 'medium', label: t`Medium` },
                  { value: 'high', label: t`High` }
                ]}
                {...form.getInputProps('priority')}
              />
              <Select
                label={t`Criticality`}
                data={[
                  { value: 'low', label: t`Low` },
                  { value: 'medium', label: t`Medium` },
                  { value: 'high', label: t`High` },
                  { value: 'critical', label: t`Critical` }
                ]}
                {...form.getInputProps('criticality')}
              />
            </SimpleGrid>

            <Textarea
              label={t`Fault or work scope`}
              placeholder={t`What is wrong, or what needs doing?`}
              autosize
              minRows={4}
              maxRows={12}
              {...form.getInputProps('faultSummary')}
            />

            <Textarea
              label={t`Symptom`}
              placeholder={t`What the operator observes`}
              autosize
              minRows={2}
              maxRows={5}
              {...form.getInputProps('symptom')}
            />

            <TextInput
              label={t`Production impact`}
              placeholder={t`What this costs while it is unresolved`}
              {...form.getInputProps('productionImpact')}
            />

            <TradeRequirementsFields
              value={form.values.requiredTrades}
              confirmed={form.values.tradeRequirementsConfirmed}
              onChange={(value) => form.setFieldValue('requiredTrades', value)}
              onConfirm={(value) =>
                form.setFieldValue('tradeRequirementsConfirmed', value)
              }
              disabled={saving || !staffAccess.plan}
            />
            <Stack gap={3} style={{ flex: 1, minWidth: 0 }}>
              <WorkOrderAssigneeSelect
                machineId={
                  form.values.machine ? Number(form.values.machine) : null
                }
                value={form.values.assignedTo}
                onChange={setAssignedTo}
                onStaffChange={setSelectedStaff}
                requiredTrades={form.values.requiredTrades}
                confirmed={form.values.tradeRequirementsConfirmed}
                suggestedUsername={suggestedOwner}
                disabled={
                  command.busy || Boolean(command.intent) || !staffAccess.assign
                }
              />
              {form.errors.assignedTo && (
                <Text size='xs' c='red' role='alert'>
                  {form.errors.assignedTo}
                </Text>
              )}
            </Stack>
            {assignmentMismatch && (
              <Alert
                color='orange'
                title={t`Trade mismatch — human review required`}
              >
                <Stack gap='sm'>
                  {staffAccess.override ? (
                    <>
                      <Textarea
                        label={t`Override reason`}
                        required
                        {...form.getInputProps('assignmentOverrideReason')}
                      />
                      <Checkbox
                        label={t`I explicitly authorize this trade mismatch override`}
                        {...form.getInputProps('assignmentOverrideConfirmed', {
                          type: 'checkbox'
                        })}
                      />
                    </>
                  ) : (
                    <Text size='sm'>{t`You do not have permission to override a trade mismatch. Create the work unassigned or select a qualified person.`}</Text>
                  )}
                </Stack>
              </Alert>
            )}
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing='md'>
              <DateInput
                label={t`Due date`}
                placeholder={t`Pick a date`}
                valueFormat='MMM D, YYYY'
                clearable
                {...form.getInputProps('dueDate')}
              />
              <NumberInput
                label={t`Estimated minutes`}
                placeholder={t`Planned effort`}
                min={1}
                {...form.getInputProps('estimatedMinutes')}
              />
            </SimpleGrid>

            <Stack gap='xs'>
              <Text size='sm' fw={500}>{t`Required parts`}</Text>
              <TextInput
                placeholder={t`Search parts by name or IPN…`}
                value={partSearch}
                onChange={(event) => setPartSearch(event.currentTarget.value)}
                rightSection={
                  partSearchLoading ? <Loader size='xs' /> : undefined
                }
              />

              {partResults.length > 0 && (
                <Paper
                  withBorder
                  p='xs'
                  style={{ maxHeight: 160, overflowY: 'auto' }}
                >
                  <Stack gap={2}>
                    {partResults.map((result) => (
                      <Button
                        key={result.pk}
                        variant='subtle'
                        size='xs'
                        justify='flex-start'
                        fullWidth
                        onClick={() =>
                          addPart(
                            result.pk,
                            result.IPN
                              ? `${result.name} (${result.IPN})`
                              : result.name
                          )
                        }
                        disabled={parts.some(
                          (part) => part.partId === result.pk
                        )}
                      >
                        {result.IPN
                          ? `${result.name}  •  ${result.IPN}`
                          : result.name}
                      </Button>
                    ))}
                  </Stack>
                </Paper>
              )}

              {parts.map((part) => (
                <Group key={part.partId} gap='xs' align='center'>
                  <Text size='sm' style={{ flex: 1 }} lineClamp={1}>
                    {part.partName}
                  </Text>
                  <NumberInput
                    size='xs'
                    min={1}
                    value={part.quantity}
                    onChange={(value) =>
                      setParts((current) =>
                        current.map((entry) =>
                          entry.partId === part.partId
                            ? {
                                ...entry,
                                quantity: typeof value === 'number' ? value : 1
                              }
                            : entry
                        )
                      )
                    }
                    style={{ width: 80 }}
                    aria-label={t`Quantity`}
                  />
                  <CloseButton
                    size='xs'
                    aria-label={t`Remove part`}
                    onClick={() =>
                      setParts((current) =>
                        current.filter((entry) => entry.partId !== part.partId)
                      )
                    }
                  />
                </Group>
              ))}
            </Stack>

            {duplicates !== null && (
              <Alert
                color='orange'
                variant='light'
                icon={<IconAlertTriangle size={16} />}
                title={t`This machine already has open repair work`}
                styles={{
                  message: { color: 'var(--mantine-color-text)' },
                  title: { color: 'var(--mantine-color-text)' }
                }}
              >
                <Stack gap='xs'>
                  {duplicates.map((duplicate) => (
                    <Anchor
                      key={duplicate.work_order_id}
                      component={Link}
                      to={`/maintenance/work-orders/${duplicate.work_order_id}/`}
                      size='sm'
                      target='_blank'
                      rel='noopener noreferrer'
                      c='var(--mantine-color-text)'
                      td='underline'
                    >
                      {duplicate.work_order_reference ||
                        t`Work order ${duplicate.work_order_id}`}
                      {duplicate.title ? ` · ${duplicate.title}` : ''}
                      {` · ${duplicateSummary(duplicate)}`}
                    </Anchor>
                  ))}
                  <Text size='sm'>
                    {t`Open the existing repair instead, or say why a second one is needed.`}
                  </Text>
                  <Textarea
                    label={t`Reason for a second repair`}
                    placeholder={t`e.g. separate fault on a different subsystem`}
                    value={overrideReason}
                    autosize
                    minRows={3}
                    maxRows={6}
                    onChange={(event) =>
                      setOverrideReason(event.currentTarget.value)
                    }
                  />
                </Stack>
              </Alert>
            )}

            <Switch
              label={t`Create a repair packet`}
              description={t`Adds the fault-to-fix aggregate: diagnosis, required parts and safety gates. Creating it plans the repair; it does not start it.`}
              styles={{ description: { color: 'var(--mantine-color-text)' } }}
              checked={form.values.createPacket}
              onChange={(event) => {
                setPacketChoiceTouched(true);
                form.setFieldValue('createPacket', event.currentTarget.checked);
              }}
            />

            {reviewSource && (
              <Checkbox
                checked={reviewed}
                onChange={(event) => setReviewed(event.currentTarget.checked)}
                label={t`I reviewed the scope, source, owner and exercise limitations.`}
              />
            )}
            <Group justify='space-between'>
              <Badge variant='light' color='gray'>
                {t`Planned work — not started`}
              </Badge>
              <Group justify='flex-end'>
                <Button variant='default' type='button' onClick={handleClose}>
                  {t`Cancel`}
                </Button>
                <Button
                  type='submit'
                  loading={saving}
                  color={
                    duplicates === null ? `${theme.primaryColor}.8` : 'orange.9'
                  }
                  autoContrast
                  vars={
                    duplicates === null
                      ? undefined
                      : () => ({ root: { '--button-color': '#000000' } })
                  }
                  disabled={
                    assignmentBlocked ||
                    command.storageFailed ||
                    Boolean(machineId && anomalyId && !sourceReady) ||
                    (duplicates !== null && !overrideReason.trim()) ||
                    (reviewSource !== null && !reviewed)
                  }
                >
                  {duplicates === null
                    ? t`Create work order`
                    : t`Create a second work order`}
                </Button>
              </Group>
            </Group>
          </Stack>
        </fieldset>
      </form>
    </Modal>
  );
}

export default WorkOrderCreateModal;
