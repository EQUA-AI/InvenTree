import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  Stack,
  Text
} from '@mantine/core';
import { IconSparkles } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';

export interface IntakeFields {
  title: string;
  work_scope: string;
  symptom: string;
  production_impact: string;
}

interface PreparedScenario {
  fields: IntakeFields;
  suggested_owner_username: string;
  priority: string;
  criticality: string;
  work_order_type: string;
  limitations: string;
}

interface IntakeContext {
  context: {
    machine: { id: number; name: string };
    source: {
      anomaly_id: number;
      title: string;
      parameter: string | null;
      synthetic: boolean;
      test_value: number | null;
      measured_value: number | null;
      unit: string;
      measurement_stale: boolean | null;
      measurement_quality: string | null;
      work_order_id: number | null;
    };
  };
  provider: { available: boolean; reason: string };
  can_create: boolean;
  prepared_scenario: PreparedScenario | null;
}

export interface IntakeDraft {
  fields: IntakeFields;
  draft_token?: string;
  provider?: { model: string; run_id: string };
  prepared?: PreparedScenario;
}

export function WorkIntakePanel({
  machineId,
  anomalyId,
  disabled,
  autoDraft,
  onReadyChange,
  onApply
}: Readonly<{
  machineId: number;
  anomalyId: number;
  disabled: boolean;
  autoDraft: boolean;
  onReadyChange: (ready: boolean) => void;
  onApply: (draft: IntakeDraft) => void;
}>) {
  const api = useApi();
  const queryClient = useQueryClient();
  const generation = useUserState((state) => state.authGeneration);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldownUntil, setCooldownUntil] = useState(0);
  const mounted = useRef(true);
  const attempted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (!cooldownUntil) return;
    const timer = window.setTimeout(
      () => setCooldownUntil(0),
      Math.max(0, cooldownUntil - Date.now())
    );
    return () => window.clearTimeout(timer);
  }, [cooldownUntil]);
  useEffect(() => () => onReadyChange(false), [onReadyChange]);
  const context = useQuery<IntakeContext>({
    queryKey: ['work-intake', machineId, anomalyId, generation],
    queryFn: async () =>
      (
        await api.get('/api/maintenance/work-packages/intake/', {
          params: { machine_id: machineId, anomaly_id: anomalyId }
        })
      ).data,
    staleTime: 0,
    retry: false
  });
  const generate = useCallback(async () => {
    if (cooldownUntil > Date.now()) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.post<IntakeDraft>(
        '/api/maintenance/work-packages/intake/draft/',
        {
          machine_id: machineId,
          anomaly_id: anomalyId
        }
      );
      if (
        mounted.current &&
        useUserState.getState().authGeneration === generation
      )
        onApply(data);
    } catch (failure) {
      if (
        mounted.current &&
        useUserState.getState().authGeneration === generation
      ) {
        if (isAxiosError(failure) && failure.response?.status === 429) {
          const seconds = Number(failure.response.headers?.['retry-after']);
          const delay = Number.isFinite(seconds) && seconds > 0 ? seconds : 60;
          setCooldownUntil(Date.now() + delay * 1000);
          setError(
            t`AI drafting rate limit reached. Wait ${delay} seconds before retrying; no fields were changed.`
          );
        } else if (
          isAxiosError(failure) &&
          failure.response?.data?.code === 'AI_INTAKE_TIMEOUT'
        ) {
          setError(
            t`AI drafting timed out. No fields were changed. Retry explicitly or use the prepared specification.`
          );
        } else if (isAxiosError(failure) && failure.response?.status === 503) {
          setError(
            t`AI provider unavailable. Use the prepared specification or continue manually; no fields were changed.`
          );
        } else if (
          isAxiosError(failure) &&
          ['ECONNABORTED', 'ETIMEDOUT'].includes(failure.code ?? '')
        ) {
          setError(
            t`AI drafting timed out. No fields were changed. Retry explicitly or use the prepared specification.`
          );
        } else {
          setError(
            t`AI could not prepare a draft. No fields were changed. Retry or continue manually.`
          );
        }
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, [api, machineId, anomalyId, generation, onApply, cooldownUntil]);
  useEffect(() => {
    onReadyChange(
      context.isSuccess &&
        !context.isFetching &&
        context.data.can_create &&
        !busy
    );
  }, [
    context.isSuccess,
    context.isFetching,
    context.dataUpdatedAt,
    context.data,
    busy,
    onReadyChange
  ]);
  useEffect(() => {
    if (
      !attempted.current &&
      autoDraft &&
      !disabled &&
      context.data?.can_create &&
      context.data.provider.available
    ) {
      attempted.current = true;
      const attemptKey = [
        'work-intake-auto-attempt',
        machineId,
        anomalyId,
        generation
      ];
      if (!queryClient.getQueryData(attemptKey)) {
        queryClient.setQueryData(attemptKey, true);
        void generate();
      }
    }
  }, [
    autoDraft,
    disabled,
    context.data,
    generate,
    queryClient,
    machineId,
    anomalyId,
    generation
  ]);
  if (context.isPending)
    return (
      <Group aria-live='polite'>
        <Loader size='xs' />
        <Text size='sm'>{t`Checking source and AI drafting…`}</Text>
      </Group>
    );
  if (context.isError || !context.data)
    return (
      <Alert color='orange' title={t`Source context unavailable`}>
        <Stack gap='xs'>
          <Text size='sm'>{t`No AI draft was applied. Reload the source before creating linked work.`}</Text>
          <Button
            variant='default'
            size='xs'
            onClick={() => void context.refetch()}
          >{t`Retry source check`}</Button>
        </Stack>
      </Alert>
    );
  const value = context.data;
  const source = value.context.source;
  return (
    <Stack gap='sm' data-testid='work-intake-panel'>
      <Alert
        color={source.synthetic ? 'orange' : 'blue'}
        variant='light'
        title={t`Source event #${source.anomaly_id}`}
        styles={{
          message: { color: 'var(--mantine-color-text)' },
          title: { color: 'var(--mantine-color-text)' }
        }}
      >
        <Stack gap={4}>
          <Text size='sm' fw={600}>
            {value.context.machine.name}
          </Text>
          <Text size='sm'>{source.parameter ?? source.title}</Text>
          {source.synthetic && (
            <>
              <Badge
                color='orange'
                size='sm'
                variant='outline'
                style={{ color: 'var(--mantine-color-text)' }}
              >{t`TEST — not a plant measurement`}</Badge>
              <Text size='sm'>
                {t`Test`}: {source.test_value ?? '—'} {source.unit} ·{' '}
                {t`Measured`}:{' '}
                {source.measurement_quality === 'good' &&
                !source.measurement_stale
                  ? (source.measured_value ?? '—')
                  : '—'}{' '}
                {source.unit}
                {source.measurement_stale ? ` · ${t`stale reference`}` : ''}
              </Text>
              <Text size='xs'>
                {t`Quality`}: {source.measurement_quality ?? t`Unknown`}
              </Text>
            </>
          )}
          {!value.can_create && (
            <Text size='sm'>{t`This event is ended or already has owning work. Do not create a second owner.`}</Text>
          )}
          {source.work_order_id && (
            <Anchor
              component={Link}
              c='var(--mantine-color-text)'
              td='underline'
              to={`/maintenance/work-orders/${source.work_order_id}/`}
            >{t`Open owning work order`}</Anchor>
          )}
        </Stack>
      </Alert>
      {value.can_create && (
        <>
          <Group align='flex-start' justify='space-between'>
            <Stack gap={2} style={{ flex: '1 1 230px' }}>
              <Text size='sm' fw={600}>
                {value.provider.available
                  ? t`Draft fields with AIMMS`
                  : t`AI drafting unavailable`}
              </Text>
              <Text size='xs'>
                {value.provider.available
                  ? t`AI prepares the form only. Review every field before creating work.`
                  : value.provider.reason === 'drafting_disabled'
                    ? t`AI drafting is disabled for this deployment. You can still complete the form manually.`
                    : t`No configured model is available here. Prepared specifications are never presented as AI output.`}
              </Text>
            </Stack>
            <Button
              type='button'
              size='xs'
              variant='light'
              leftSection={<IconSparkles size={15} />}
              disabled={
                disabled ||
                !value.provider.available ||
                cooldownUntil > Date.now()
              }
              loading={busy}
              onClick={() => void generate()}
            >{t`Draft with AIMMS AI`}</Button>
          </Group>
          {value.prepared_scenario && (
            <Button
              type='button'
              variant='default'
              size='xs'
              disabled={disabled || busy}
              onClick={() =>
                onApply({
                  fields: value.prepared_scenario!.fields,
                  prepared: value.prepared_scenario!
                })
              }
            >{t`Use prepared Pump 08 specification`}</Button>
          )}
        </>
      )}
      {busy && (
        <Text
          size='xs'
          aria-live='polite'
        >{t`Preparing an AI draft. Your form remains unchanged until a valid response arrives.`}</Text>
      )}
      {error && (
        <Alert
          color='orange'
          role='alert'
          styles={{ message: { color: 'var(--mantine-color-text)' } }}
        >
          {error}
        </Alert>
      )}
    </Stack>
  );
}
