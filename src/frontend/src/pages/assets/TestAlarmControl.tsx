import type {
  MachineSignalLimits,
  SignalQuality
} from '@lib/types/MachineHealth';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  Textarea
} from '@mantine/core';
import { IconExternalLink } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { useEffect, useId, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useApi } from '../../contexts/ApiContext';
import { useRetainedCommand } from '../../hooks/useRetainedCommand';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import type { IntakeFields } from '../maintenance/components/WorkIntakePanel';
import { WorkOrderAssigneeSelect } from '../maintenance/components/WorkOrderAssigneeSelect';
import {
  WorkOrderCreateModal,
  showWorkPackageWarnings
} from '../maintenance/components/WorkOrderCreateModal';
import type {
  DuplicateRepair,
  WorkPackageResult
} from '../maintenance/components/WorkOrderCreateModal';
import { duplicateSummary } from '../maintenance/components/duplicateSummary';
import { formatSignalLimits } from './health/common';
import { invalidateTestAlarmViews } from './invalidateTestAlarmViews';
import { formatValue, unitLabel } from './performance/format';

interface Equipment {
  pk: number;
  name: string;
  asset_type?: string;
  parent?: number | null;
}
interface Target {
  binding_id: number;
  name: string;
  component_id: number | null;
  component_name: string | null;
  component_code: string | null;
  unit: string;
  critical_max: number | null;
  external_key?: string;
  value?: number | string | boolean | null;
  quality?: SignalQuality;
  stale?: boolean;
  limits?: MachineSignalLimits;
  eligible?: boolean;
  ineligible_reason?: 'existing_test' | 'non_numeric' | null;
  value_floor?: number | null;
  suggested_test_value?: number | null;
  limit_source?: 'configured' | 'unconfigured';
}
interface AlarmReceipt {
  anomaly_id: number;
  machine_id: number;
  status: string;
  test_value: number;
  unit: string;
  idempotency_key: string;
  binding_id: number;
  work_order?: WorkPackageResult & {
    assignment?: { supervisor_escalation?: string; code: string };
    triage?: { explanation: string };
  };
}

interface WorkPlan {
  plan_token: string;
  scenario: {
    label: string;
    fields: IntakeFields;
    limitations: string;
    suggested_owner_username: string | null;
    priority: string;
    criticality: string;
    work_order_type: string;
    parts: unknown[];
  };
  duplicates: DuplicateRepair[];
}

function planValueLabel(value: string): string {
  return (
    (
      {
        corrective: t`Corrective`,
        preventive: t`Preventive`,
        inspection: t`Inspection`,
        calibration: t`Calibration`,
        other: t`Other`,
        low: t`Low`,
        medium: t`Medium`,
        high: t`High`,
        critical: t`Critical`
      } as Record<string, string>
    )[value] ?? t`Not provided`
  );
}

export function TestAlarmControl({
  machine
}: Readonly<{ machine: Equipment }>) {
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const readinessId = useId();
  const actor = useUserState((state) => state.user?.pk);
  const generation = useUserState((state) => state.authGeneration);
  const host = useLocalState((state) => state.hostKey);
  const [opened, setOpened] = useState(false);
  const [selectedStation, setStation] = useState<string | null>(
    machine.asset_type === 'pumphouse'
      ? String(machine.pk)
      : machine.parent
        ? String(machine.parent)
        : null
  );
  const [selectedPump, setPump] = useState<string | null>(
    machine.asset_type === 'pumphouse' ? null : String(machine.pk)
  );
  const [selectedComponent, setComponent] = useState<string | null>(null);
  const [selectedBinding, setBinding] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [testValue, setTestValue] = useState<number | string>('');
  const [automaticWork, setAutomaticWork] = useState(true);
  const [eventWork, setEventWork] = useState(true);
  const [assignedTo, setAssignedTo] = useState<string | null>(null);
  const [overrideReason, setOverrideReason] = useState('');
  const [overrideConfirmed, setOverrideConfirmed] = useState(false);
  const retained = useRetainedCommand(
    `test-alarm:${machine.pk}`,
    /^\/api\/machine-health\/machines\/\d+\/health\/test-alarm\/$/
  );
  const { busy, intent } = retained;
  const station = intent?.context?.station ?? selectedStation;
  const component = intent
    ? String(intent.body.component_id ?? 'machine')
    : selectedComponent;
  const binding = intent ? String(intent.body.binding_id) : selectedBinding;
  const pump = intent?.url.match(/\/machines\/(\d+)\//)?.[1] ?? selectedPump;
  const [receipt, setReceipt] = useState<AlarmReceipt | null>(null);
  const [createWork, setCreateWork] = useState(false);
  const identity = [host, actor, generation];
  const stations = useQuery({
    queryKey: ['test-alarm-stations', ...identity],
    enabled: opened,
    retry: false,
    queryFn: async () =>
      (
        await api.get<{ results: Equipment[] }>(
          '/api/assets/registry/?limit=500'
        )
      ).data.results
  });
  const pumps = useQuery({
    queryKey: ['test-alarm-pumps', ...identity, station],
    enabled: opened && !!station,
    retry: false,
    queryFn: async () =>
      (
        await api.get<{ children: Equipment[] }>(
          `/api/assets/registry/${station}/`
        )
      ).data.children
  });
  const targetQuery = useQuery({
    queryKey: ['test-alarm-targets', ...identity, pump],
    enabled: opened && !!pump,
    retry: false,
    queryFn: async () =>
      (
        await api.get<{ results: Target[]; event_work_enabled?: boolean }>(
          `/api/machine-health/machines/${pump}/health/test-alarm/`
        )
      ).data
  });
  const targets =
    !targetQuery.isError && !targetQuery.isFetching
      ? (targetQuery.data?.results ?? [])
      : [];
  const components = [
    ...new Map(
      targets.map((target) => [
        String(target.component_id ?? 'machine'),
        {
          value: String(target.component_id ?? 'machine'),
          label: target.component_name
            ? `${target.component_name} · ${target.component_code}`
            : t`Pump-level signals`
        }
      ])
    ).values()
  ];
  const selected = targets.find(
    (target) =>
      String(target.binding_id) === binding &&
      (!component || String(target.component_id ?? 'machine') === component)
  );
  const eventOwned = intent
    ? intent.body.event_owned_work === true
    : automaticWork &&
      eventWork &&
      Boolean(targetQuery.data?.event_work_enabled);
  const plan = useQuery<WorkPlan>({
    queryKey: ['test-alarm-work-plan', ...identity, pump, binding],
    enabled: opened && automaticWork && !!selected && !intent && !receipt,
    retry: false,
    staleTime: 0,
    queryFn: async () =>
      (
        await api.get(
          `/api/machine-health/machines/${pump}/health/test-alarm/`,
          {
            params: { binding_id: Number(binding) }
          }
        )
      ).data
  });
  const automatic = intent
    ? intent.body.create_work_order === true ||
      intent.body.event_owned_work === true
    : automaticWork;
  const duplicates = plan.data?.duplicates ?? [];
  useEffect(() => {
    setConfirmed(false);
    setOverrideConfirmed(false);
  }, [plan.data?.plan_token]);
  useEffect(() => {
    if (retained.refusal && automaticWork) {
      setConfirmed(false);
      setOverrideConfirmed(false);
      void plan.refetch();
    }
  }, [retained.refusal, automaticWork, plan.refetch]);
  const workReady =
    !automatic ||
    (plan.isSuccess &&
      !plan.isFetching &&
      !!plan.data.plan_token &&
      (eventOwned || !!assignedTo) &&
      (!duplicates.length || (overrideConfirmed && !!overrideReason.trim())));
  const syntheticValue = intent ? Number(intent.body.test_value) : testValue;
  const floor = selected?.value_floor ?? selected?.critical_max;
  const valueValid =
    typeof syntheticValue === 'number' &&
    Number.isFinite(syntheticValue) &&
    (floor == null || syntheticValue > floor);
  const locked = busy || !!intent || !!receipt || retained.storageFailed;
  const previewDetail = isAxiosError(plan.error)
    ? plan.error.response?.data?.detail
    : null;
  const previewMessage =
    typeof previewDetail === 'string'
      ? previewDetail.slice(0, 1000)
      : Array.isArray(previewDetail)
        ? previewDetail
            .filter((detail): detail is string => typeof detail === 'string')
            .join(' ')
            .slice(0, 1000)
        : t`The work preview could not be loaded. Nothing has been triggered. Reload the preview to retry.`;
  const unmetConditions = [
    ...(retained.storageFailed
      ? [
          t`Reconcile retained command storage before triggering another request.`
        ]
      : []),
    ...(!selected ? [t`Select an eligible mapped signal.`] : []),
    ...(selected?.eligible === false
      ? [
          t`This signal cannot accept a new test. Review its existing health event or choose another eligible signal.`
        ]
      : []),
    ...(selected && !valueValid
      ? [
          t`Enter a finite synthetic value above the measured value and configured upper bounds.`
        ]
      : []),
    ...(automatic && selected && (!plan.isSuccess || plan.isFetching)
      ? [t`Wait for a valid work preview or reload it.`]
      : []),
    ...(automatic && !eventOwned && plan.isSuccess && !assignedTo
      ? [t`Select eligible assigned staff.`]
      : []),
    ...(automatic && duplicates.length && !overrideReason.trim()
      ? [t`Explain why a separate work order is needed.`]
      : []),
    ...(automatic && duplicates.length && !overrideConfirmed
      ? [t`Confirm that you reviewed the existing work.`]
      : []),
    ...(!confirmed
      ? [t`Confirm the synthetic exercise and notification request.`]
      : [])
  ];
  const send = async () => {
    if (
      !pump ||
      busy ||
      receipt ||
      (!intent &&
        (!selected ||
          !confirmed ||
          !valueValid ||
          selected.eligible === false ||
          !workReady))
    )
      return;
    const request = intent?.body ?? {
      binding_id: selected!.binding_id,
      component_id: selected!.component_id,
      test_value: Number(syntheticValue),
      confirmed: true,
      idempotency_key: crypto.randomUUID(),
      ...(eventOwned
        ? {
            event_owned_work: true,
            duplicate_override: duplicates.length > 0 && overrideConfirmed,
            duplicate_override_reason: duplicates.length
              ? overrideReason.trim()
              : ''
          }
        : automatic
          ? {
              create_work_order: true,
              plan_token: plan.data!.plan_token,
              assigned_to_id: Number(assignedTo),
              duplicate_override: duplicates.length > 0 && overrideConfirmed,
              duplicate_override_reason: duplicates.length
                ? overrideReason.trim()
                : ''
            }
          : {})
    };
    const data = await retained.execute<AlarmReceipt>(
      {
        url: `/api/machine-health/machines/${pump}/health/test-alarm/`,
        body: request,
        context: {
          station,
          workTitle: automatic
            ? (plan.data?.scenario.fields.title ?? null)
            : null
        }
      },
      (response, request) =>
        response.machine_id === Number(pump) &&
        Number.isSafeInteger(response.anomaly_id) &&
        response.anomaly_id > 0 &&
        response.idempotency_key === request.body.idempotency_key &&
        response.binding_id === request.body.binding_id &&
        ((request.body.create_work_order !== true &&
          request.body.event_owned_work !== true) ||
          (Number.isSafeInteger(response.work_order?.work_order_id) &&
            Number(response.work_order?.work_order_id) > 0 &&
            Number.isSafeInteger(response.work_order?.repair_packet_id) &&
            Number(response.work_order?.repair_packet_id) > 0 &&
            response.work_order?.machine_id === response.machine_id &&
            response.work_order?.anomaly_id === response.anomaly_id &&
            response.test_value === request.body.test_value &&
            (request.body.event_owned_work === true ||
              response.work_order?.assigned_to_id ===
                request.body.assigned_to_id) &&
            response.work_order?.idempotency_key ===
              `test-alarm-work:${actor}:${request.body.idempotency_key}`))
    );
    if (data) {
      setAutomaticWork(
        request.create_work_order === true || request.event_owned_work === true
      );
      setStation(station);
      setPump(String(data.machine_id));
      setComponent(String(request.component_id ?? 'machine'));
      setBinding(String(request.binding_id));
      setReceipt(data);
      await invalidateTestAlarmViews(queryClient);
      if (data.work_order) {
        showWorkPackageWarnings(data.work_order.warnings);
        await queryClient.invalidateQueries({ queryKey: ['work-orders'] });
        if (!eventOwned) {
          setOpened(false);
          navigate(
            `/maintenance/work-orders/${data.work_order.work_order_id}/`
          );
        }
      }
    }
  };
  return (
    <>
      <Button
        variant='light'
        color='orange'
        styles={{ label: { color: 'var(--mantine-color-text)' } }}
        onClick={() => setOpened(true)}
      >{t`Trigger test alarm`}</Button>
      <Modal
        opened={opened}
        onClose={() => setOpened(false)}
        title={t`Trigger test alarm`}
        size='lg'
        closeOnClickOutside={!busy}
        closeOnEscape={!busy}
        withCloseButton={!busy}
        closeButtonProps={{ 'aria-label': t`Close test alarm` }}
      >
        <Stack>
          <Alert
            color='orange'
            title={t`Synthetic exercise — not a plant measurement`}
            styles={{
              message: { color: 'var(--mantine-color-text)' },
              title: { color: 'var(--mantine-color-text)' }
            }}
          >
            {t`Creates a labelled alarm and requests email and in-app notification through configured channels. It never changes real readings or writes to SCADA.`}
          </Alert>
          {!receipt && (
            <Stack>
              {stations.isLoading ? (
                <Loader size='sm' />
              ) : stations.isError ? (
                <Alert color='red'>
                  {t`Stations unavailable.`}{' '}
                  <Button
                    variant='subtle'
                    onClick={() => void stations.refetch()}
                  >{t`Retry`}</Button>
                </Alert>
              ) : (
                <Select
                  label={t`Pump station`}
                  searchable
                  clearable
                  value={station}
                  disabled={locked}
                  data={(stations.data ?? []).map((row) => ({
                    value: String(row.pk),
                    label: row.name
                  }))}
                  onChange={(value) => {
                    setStation(value);
                    setAssignedTo(null);
                    setOverrideReason('');
                    setOverrideConfirmed(false);
                    setPump(null);
                    setComponent(null);
                    setBinding(null);
                    setConfirmed(false);
                  }}
                />
              )}
              {pumps.isError && (
                <Alert color='red'>
                  {t`Pumps unavailable.`}{' '}
                  <Button
                    onClick={() => void pumps.refetch()}
                  >{t`Retry`}</Button>
                </Alert>
              )}
              <Select
                label={t`Pump / equipment`}
                value={pump}
                searchable
                disabled={
                  locked || (!!station && (pumps.isFetching || pumps.isError))
                }
                data={(station ? (pumps.data ?? []) : [machine]).map((row) => ({
                  value: String(row.pk),
                  label: row.name
                }))}
                onChange={(value) => {
                  setPump(value);
                  setAssignedTo(null);
                  setOverrideReason('');
                  setOverrideConfirmed(false);
                  setComponent(null);
                  setBinding(null);
                  setConfirmed(false);
                }}
              />
              {targetQuery.isLoading && <Loader size='sm' />}
              {targetQuery.isError && (
                <Alert color='red'>
                  {t`Test controls unavailable. An administrator must enable test alarms for this deployment.`}
                  <Button
                    variant='subtle'
                    onClick={() => void targetQuery.refetch()}
                  >{t`Retry`}</Button>
                </Alert>
              )}
              <Select
                label={t`Equipment component`}
                description={t`Registered signal mapping — not confirmation of a physical fit or operating authority.`}
                styles={{ description: { color: 'var(--mantine-color-text)' } }}
                searchable
                clearable
                placeholder={t`All components`}
                value={component}
                data={components}
                disabled={locked || !targets.length}
                onChange={(value) => {
                  setComponent(value);
                  setOverrideReason('');
                  setOverrideConfirmed(false);
                  setBinding(null);
                  setConfirmed(false);
                }}
              />
              <Select
                label={t`Configured test signal`}
                value={binding}
                searchable
                disabled={locked || !targets.length}
                description={t`All mapped parameters are listed. Status and non-numeric channels cannot simulate a numeric reading.`}
                styles={{ description: { color: 'var(--mantine-color-text)' } }}
                data={targets
                  .filter(
                    (target) =>
                      !component ||
                      String(target.component_id ?? 'machine') === component
                  )
                  .map((target) => ({
                    value: String(target.binding_id),
                    label: target.external_key
                      ? `${target.name} · ${target.external_key}`
                      : target.name,
                    disabled: target.eligible === false
                  }))}
                renderOption={({ option }) => {
                  const target = targets.find(
                    (row) => String(row.binding_id) === option.value
                  )!;
                  return (
                    <Stack gap={2} miw={0}>
                      <Text size='sm' style={{ overflowWrap: 'anywhere' }}>
                        {option.label}
                      </Text>
                      <Text size='xs'>
                        {t`Measured`}:{' '}
                        {typeof target.value === 'number' &&
                        !target.stale &&
                        target.quality === 'good'
                          ? formatValue(target.value, 2, target.unit)
                          : '—'}
                        {target.stale ? ` · ${t`Stale`}` : ''}
                      </Text>
                      <Text size='xs'>
                        {target.limits
                          ? formatSignalLimits({
                              limits: target.limits,
                              unit: target.unit
                            }) || t`No limits configured`
                          : t`No limits configured`}
                      </Text>
                      {target.ineligible_reason && (
                        <Text size='xs'>
                          {target.ineligible_reason === 'existing_test'
                            ? t`An existing test must be ended first.`
                            : t`Status or non-numeric channel — numeric tests unavailable.`}
                        </Text>
                      )}
                    </Stack>
                  );
                }}
                onChange={(value) => {
                  setBinding(value);
                  setOverrideReason('');
                  setOverrideConfirmed(false);
                  const target = targets.find(
                    (row) => String(row.binding_id) === value
                  );
                  setTestValue(
                    target?.suggested_test_value ??
                      (target?.critical_max != null
                        ? target.critical_max +
                          Math.max(Math.abs(target.critical_max) * 0.1, 0.1)
                        : '')
                  );
                  setConfirmed(false);
                }}
              />
              {pump && targetQuery.isSuccess && !targets.length && (
                <Text size='sm'>{t`No active mapped parameters are available for this pump.`}</Text>
              )}
              {selected && (
                <Stack gap='xs'>
                  <Text size='sm'>
                    {t`Measured now`}:{' '}
                    {typeof selected.value === 'number' &&
                    !selected.stale &&
                    selected.quality === 'good'
                      ? formatValue(selected.value, 2, selected.unit)
                      : '—'}
                  </Text>
                  <Text size='sm'>
                    {selected.limits
                      ? formatSignalLimits({
                          limits: selected.limits,
                          unit: selected.unit
                        }) || t`No limits configured`
                      : selected.critical_max != null
                        ? `${t`Configured critical limit`}: ${formatValue(selected.critical_max, 1, selected.unit)}`
                        : t`No limits configured`}
                  </Text>
                  <NumberInput
                    label={t`Synthetic test value`}
                    styles={{
                      description: { color: 'var(--mantine-color-text)' }
                    }}
                    suffix={
                      selected.unit ? ` ${unitLabel(selected.unit)}` : undefined
                    }
                    value={syntheticValue}
                    disabled={locked}
                    allowDecimal
                    hideControls
                    onChange={(value) => {
                      setTestValue(value);
                      setConfirmed(false);
                    }}
                    description={
                      selected.critical_max != null
                        ? t`Must exceed the measured value and all configured upper bounds. Only the labelled TEST view changes.`
                        : t`No configured critical limit. Critical is the exercise severity, not an engineering verdict. No limit is created.`
                    }
                    error={
                      !valueValid && syntheticValue !== ''
                        ? t`Enter a finite value above the measured value and configured upper bounds.`
                        : undefined
                    }
                  />
                </Stack>
              )}
              <Switch
                role='switch'
                aria-label={t`Create work order automatically`}
                label={t`Create work order automatically`}
                description={t`One confirmed action creates the alarm, planned work order and draft repair packet together.`}
                styles={{ description: { color: 'var(--mantine-color-text)' } }}
                checked={automatic}
                disabled={locked}
                onChange={(event) => {
                  setAutomaticWork(event.currentTarget.checked);
                  setConfirmed(false);
                }}
              />
              {automatic && targetQuery.data?.event_work_enabled && !intent && (
                <Checkbox
                  label={t`Let the event prepare work and request automatic assignment`}
                  description={t`If staffing evidence is incomplete, work remains unassigned for supervisor review. Existing open work requires review.`}
                  checked={eventWork}
                  disabled={locked}
                  onChange={(event) => {
                    setEventWork(event.currentTarget.checked);
                    setOverrideConfirmed(false);
                    setConfirmed(false);
                  }}
                />
              )}
              {automatic && selected && !intent && (
                <Stack gap='sm' data-testid='test-alarm-work-plan'>
                  {plan.isFetching ? (
                    <Group aria-live='polite'>
                      <Loader size='xs' />
                      <Text size='sm'>{t`Preparing work-order preview…`}</Text>
                    </Group>
                  ) : plan.isError ? (
                    <Alert
                      color='red'
                      title={t`Work preview unavailable`}
                      role='alert'
                      styles={{
                        message: { color: 'var(--mantine-color-text)' },
                        title: { color: 'var(--mantine-color-text)' }
                      }}
                    >
                      <Text size='sm'>{previewMessage}</Text>
                      <Text size='sm'>{t`Nothing will be triggered until the work preview is ready. Check creation permissions or reload.`}</Text>
                      <Text size='sm'>{t`You can also turn off automatic work-order creation to trigger only the alarm. Test-alarm permissions still apply; no work order will be created.`}</Text>
                      <Button
                        variant='default'
                        size='xs'
                        onClick={() => void plan.refetch()}
                      >{t`Reload work preview`}</Button>
                    </Alert>
                  ) : (
                    plan.data && (
                      <>
                        <Alert
                          color='blue'
                          title={t`Prepared work order — review before triggering`}
                          styles={{
                            message: { color: 'var(--mantine-color-text)' },
                            title: { color: 'var(--mantine-color-text)' }
                          }}
                        >
                          <Stack gap='xs'>
                            <Text fw={600} size='sm'>
                              {plan.data.scenario.fields.title}
                            </Text>
                            <Text size='sm'>
                              {plan.data.scenario.fields.work_scope}
                            </Text>
                            <Text size='sm'>
                              <Text span fw={600}>
                                {t`Symptom`}:{' '}
                              </Text>
                              {plan.data.scenario.fields.symptom}
                            </Text>
                            <Text size='sm'>
                              {plan.data.scenario.fields.production_impact}
                            </Text>
                            <Text size='xs'>
                              {plan.data.scenario.limitations}
                            </Text>
                            <Text size='sm'>{t`Trade requirements are not recorded for this prepared exercise. Staff suitability needs human review.`}</Text>
                            <Text size='xs'>
                              {t`Type`}:{' '}
                              {planValueLabel(
                                plan.data.scenario.work_order_type
                              )}{' '}
                              · {t`Priority`}:{' '}
                              {planValueLabel(plan.data.scenario.priority)} ·{' '}
                              {t`Criticality`}:{' '}
                              {planValueLabel(plan.data.scenario.criticality)}
                            </Text>
                            <Text size='xs'>
                              {plan.data.scenario.parts.length === 0
                                ? t`No parts requested.`
                                : t`Parts requested: ${plan.data.scenario.parts.length}`}
                            </Text>
                            <Text size='xs'>{t`Creation does not start, approve or close the work.`}</Text>
                          </Stack>
                        </Alert>
                        {!eventOwned && (
                          <WorkOrderAssigneeSelect
                            key={pump}
                            machineId={Number(pump)}
                            value={assignedTo}
                            onChange={(value) => {
                              setAssignedTo(value);
                              setConfirmed(false);
                            }}
                            suggestedUsername={
                              plan.data.scenario.suggested_owner_username
                            }
                            disabled={locked}
                          />
                        )}
                        {duplicates.length > 0 && (
                          <Alert
                            color='orange'
                            title={t`Existing work needs review`}
                            styles={{
                              message: { color: 'var(--mantine-color-text)' },
                              title: { color: 'var(--mantine-color-text)' }
                            }}
                          >
                            <Stack gap='xs'>
                              {duplicates.map((duplicate) => (
                                <Stack key={duplicate.work_order_id} gap={2}>
                                  <Anchor
                                    component={Link}
                                    to={`/maintenance/work-orders/${duplicate.work_order_id}/`}
                                    target='_blank'
                                    rel='noopener noreferrer'
                                    aria-label={`${duplicate.work_order_reference || `#${duplicate.work_order_id}`} · ${duplicate.title ?? t`Existing repair`} ${t`(opens in a new tab)`}`}
                                    c='var(--mantine-color-text)'
                                    td='underline'
                                  >
                                    {duplicate.work_order_reference ||
                                      `#${duplicate.work_order_id}`}{' '}
                                    · {duplicate.title ?? t`Existing repair`}{' '}
                                    {t`(opens in a new tab)`}
                                    <IconExternalLink
                                      size={14}
                                      aria-hidden='true'
                                      style={{
                                        marginInlineStart: 4,
                                        verticalAlign: 'text-bottom'
                                      }}
                                    />
                                  </Anchor>
                                  <Text size='xs'>
                                    {duplicateSummary(duplicate)}
                                  </Text>
                                </Stack>
                              ))}
                              <Textarea
                                label={t`Reason for a separate work order`}
                                value={overrideReason}
                                maxLength={500}
                                autosize
                                minRows={2}
                                disabled={locked}
                                onChange={(event) => {
                                  setOverrideReason(event.currentTarget.value);
                                  setOverrideConfirmed(false);
                                  setConfirmed(false);
                                }}
                              />
                              <Checkbox
                                label={t`I reviewed existing work and need a separate work order.`}
                                checked={overrideConfirmed}
                                disabled={locked}
                                onChange={(event) => {
                                  setOverrideConfirmed(
                                    event.currentTarget.checked
                                  );
                                  setConfirmed(false);
                                }}
                              />
                            </Stack>
                          </Alert>
                        )}
                      </>
                    )
                  )}
                </Stack>
              )}
              {automatic && intent && (
                <Text size='sm'>
                  {t`Retained work-order request`}:{' '}
                  {intent.context?.workTitle ?? t`Prepared investigation`} ·{' '}
                  {eventOwned
                    ? t`Event-owned planning and staffing review`
                    : `${t`Assigned staff`} #${String(intent.body.assigned_to_id)}`}
                </Text>
              )}
              {!receipt && (
                <Checkbox
                  label={t`I confirm this is a synthetic test; notify authorized recipients.`}
                  checked={confirmed}
                  disabled={locked}
                  onChange={(event) =>
                    setConfirmed(event.currentTarget.checked)
                  }
                />
              )}
            </Stack>
          )}
          {intent && !retained.error && (
            <Alert color='blue'>{t`A previous test request is retained. Retry keeps its exact selection and request key, including after a reload.`}</Alert>
          )}
          {retained.error && (
            <Alert
              color='red'
              title={
                retained.refusal
                  ? automatic
                    ? t`Alarm and work-order request refused`
                    : t`Alarm request refused`
                  : automatic
                    ? t`Alarm and work-order outcome not confirmed`
                    : t`Alarm outcome not confirmed`
              }
              styles={{
                title: { color: 'var(--mantine-color-text)' },
                message: { color: 'var(--mantine-color-text)' }
              }}
            >
              {retained.error}
            </Alert>
          )}
          {receipt ? (
            <Alert color='blue' title={t`Test alarm recorded`}>
              <Stack gap='xs'>
                <Text size='sm' data-testid='test-alarm-recorded-target'>
                  {[
                    stations.data?.find((row) => String(row.pk) === station)
                      ?.name,
                    pumps.data?.find((row) => row.pk === receipt.machine_id)
                      ?.name ?? machine.name,
                    selected?.name ?? `#${receipt.binding_id}`
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </Text>
                <Text size='sm'>
                  {t`Synthetic value`}:{' '}
                  {formatValue(receipt.test_value, 1, receipt.unit)}.{' '}
                  {t`Actual measurements are unchanged. Email delivery depends on configured recipients and channels.`}
                </Text>
                <Anchor
                  component={Link}
                  onClick={() => setOpened(false)}
                  to={`/machines/machine/${receipt.machine_id}/health?anomaly=${receipt.anomaly_id}`}
                >{t`View test alarm`}</Anchor>
                {receipt.work_order ? (
                  <Stack gap='xs'>
                    {receipt.work_order.triage?.explanation && (
                      <Text size='sm'>
                        {receipt.work_order.triage.explanation}
                      </Text>
                    )}
                    {receipt.work_order.assignment?.supervisor_escalation && (
                      <Alert
                        color='orange'
                        title={t`Supervisor review required`}
                      >
                        {receipt.work_order.assignment.supervisor_escalation}
                      </Alert>
                    )}
                    <Button
                      component={Link}
                      to={`/maintenance/work-orders/${receipt.work_order.work_order_id}/`}
                      onClick={() => setOpened(false)}
                    >{t`Open owning work order`}</Button>
                  </Stack>
                ) : (
                  <Button
                    onClick={() => {
                      setCreateWork(true);
                      setOpened(false);
                    }}
                  >{t`Create work for this test`}</Button>
                )}
                <Button
                  variant='default'
                  onClick={() => {
                    setReceipt(null);
                    setBinding(null);
                    setConfirmed(false);
                  }}
                >{t`Start another test`}</Button>
              </Stack>
            </Alert>
          ) : (
            <Stack gap='xs'>
              <Text
                id={readinessId}
                component='output'
                aria-live='polite'
                size='sm'
                data-testid='test-alarm-readiness'
              >
                {busy
                  ? t`Submitting the reviewed request. Wait for its confirmed outcome.`
                  : intent
                    ? t`Retry the exact retained request to confirm its outcome. No replacement alarm or work order will be created.`
                    : unmetConditions.length
                      ? unmetConditions.join(' ')
                      : t`Ready to trigger the reviewed request.`}
              </Text>
              <Group justify='flex-end'>
                <Button
                  aria-describedby={readinessId}
                  aria-busy={busy}
                  color='orange.9'
                  styles={{ label: { color: 'var(--mantine-color-black)' } }}
                  loading={busy}
                  disabled={
                    retained.storageFailed ||
                    (!intent &&
                      (!confirmed ||
                        !selected ||
                        selected.eligible === false ||
                        !valueValid ||
                        !workReady))
                  }
                  onClick={() => void send()}
                >
                  {intent
                    ? t`Retry same test request`
                    : automatic
                      ? t`Trigger and create work order`
                      : t`Trigger and notify`}
                </Button>
              </Group>
            </Stack>
          )}
        </Stack>
      </Modal>
      {receipt && (
        <WorkOrderCreateModal
          opened={createWork}
          onClose={() => setCreateWork(false)}
          machineId={receipt.machine_id}
          origin='anomaly'
          anomalyId={receipt.anomaly_id}
          initialTitle={t`Investigate test alarm`}
          initialCriticality='critical'
          autoDraft
          onCreated={(result) =>
            navigate(`/maintenance/work-orders/${result.work_order_id}/`)
          }
        />
      )}
    </>
  );
}
