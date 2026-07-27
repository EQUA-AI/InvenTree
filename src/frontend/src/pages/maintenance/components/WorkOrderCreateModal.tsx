import { t } from '@lingui/core/macro';
import {
  Badge,
  Button,
  CloseButton,
  Group,
  Loader,
  Modal,
  NumberInput,
  Paper,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useForm } from '@mantine/form';
import { useDebouncedValue, useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import dayjs from 'dayjs';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';

import { useApi } from '../../../contexts/ApiContext';
import { showApiErrorMessage } from '../../../functions/notifications';

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
  dueDate: Date | null;
  estimatedMinutes: number | string;
  createPacket: boolean;
}

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
  onCreated
}: Readonly<WorkOrderCreateModalProps>) {
  const api = useApi();

  // Below Mantine's `sm` breakpoint a size='lg' modal is unusable; go
  // full-screen so the form stays reachable on a phone.
  const isSmallScreen = useMediaQuery('(max-width: 48em)');

  const [saving, setSaving] = useState(false);
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
      dueDate: null,
      estimatedMinutes: '',
      createPacket: true
    },
    validate: {
      title: (value) =>
        value.trim().length === 0
          ? t`Describe the work in a short title.`
          : null,
      // Every work order is anchored to a machine; the backend enforces this too.
      machine: (value) => (value ? null : t`Select the machine for this work.`)
    }
  });

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
    form.reset();
    setParts([]);
    setPartSearch('');
    setPartResults([]);
    setSaving(false);
    onClose();
    // form is stable for the lifetime of the modal
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose]);

  const handleSubmit = form.onSubmit(async (values) => {
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
      source: anomalyId ? { anomaly_id: anomalyId } : {},
      planning: {
        assignee: values.assignee.trim(),
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
      const response = await api.post(
        apiUrl(ApiEndpoints.maintenance_work_package_create),
        draft
      );
      setIdempotencyKey(newIdempotencyKey());
      onCreated?.(response.data as WorkPackageResult);
      handleClose();
    } catch (error) {
      showApiErrorMessage({
        error,
        title: t`Could not create the work order`
      });
      setSaving(false);
    }
  });

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title={t`New work order`}
      size='lg'
      fullScreen={isSmallScreen}
    >
      <form onSubmit={handleSubmit}>
        <Stack gap='md'>
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

          <Group align='flex-end' gap='md' grow>
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
          </Group>

          <Textarea
            label={t`Fault or work scope`}
            placeholder={t`What is wrong, or what needs doing?`}
            minRows={3}
            {...form.getInputProps('faultSummary')}
          />

          <Group align='flex-end' gap='md' grow>
            <TextInput
              label={t`Symptom`}
              placeholder={t`What the operator observes`}
              {...form.getInputProps('symptom')}
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
          </Group>

          <TextInput
            label={t`Production impact`}
            placeholder={t`What this costs while it is unresolved`}
            {...form.getInputProps('productionImpact')}
          />

          <Group align='flex-end' gap='md' grow>
            <TextInput
              label={t`Assignee`}
              placeholder={t`Who owns this work?`}
              {...form.getInputProps('assignee')}
            />
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
          </Group>

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
                      disabled={parts.some((part) => part.partId === result.pk)}
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

          <Switch
            label={t`Create a repair packet`}
            description={t`Adds the fault-to-fix aggregate: diagnosis, required parts and safety gates. Creating it plans the repair; it does not start it.`}
            checked={form.values.createPacket}
            onChange={(event) => {
              setPacketChoiceTouched(true);
              form.setFieldValue('createPacket', event.currentTarget.checked);
            }}
          />

          <Group justify='space-between'>
            <Badge variant='light' color='gray'>
              {t`Planned work — not started`}
            </Badge>
            <Group justify='flex-end'>
              <Button variant='default' type='button' onClick={handleClose}>
                {t`Cancel`}
              </Button>
              <Button type='submit' loading={saving}>
                {t`Create work order`}
              </Button>
            </Group>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}

export default WorkOrderCreateModal;
