import type { RepairPacket } from '@lib/types/Repair';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Group,
  List,
  Paper,
  Stack,
  Text,
  Title
} from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../../../contexts/ApiContext';
import { useRetainedCommand } from '../../../hooks/useRetainedCommand';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import classes from './PacketWorkLifecycle.module.css';
import { lifecycleActionDisabled } from './packetWorkPolicy';
import { matchesPacketWorkReceipt } from './packetWorkReceipt';

interface WorkState {
  id: number;
  reference: string;
  lifecycle_status: string;
  lifecycle_version: number;
}

interface Readiness {
  ready: boolean;
  lifecycle_version: number;
  blockers: { code: string; message: string }[];
}

export function PacketWorkLifecycle({
  packet,
  onChanged
}: Readonly<{ packet: RepairPacket; onChanged: () => void }>) {
  const api = useApi();
  const client = useQueryClient();
  const user = useUserState((state) => state.user);
  const generation = useUserState((state) => state.authGeneration);
  const host = useLocalState((state) => state.hostKey);
  const boundary = `${host}:${user?.pk}:${generation}:${packet.pk}`;
  const [feedback, setFeedback] = useState<{
    boundary: string;
    message: string;
    error?: boolean;
  } | null>(null);
  const [refreshIssue, setRefreshIssue] = useState<string | null>(null);
  const base = `/api/repair/packets/${packet.pk}/`;
  const command = useRetainedCommand(
    `packet-work-lifecycle:${packet.pk}`,
    new RegExp(`^${base}(work-transition|start)/$`)
  );
  const work = useQuery<WorkState>({
    queryKey: ['packet-work-lifecycle', host, packet.pk, user?.pk, generation],
    queryFn: async () =>
      (await api.get(`/api/tasks/work-orders/${packet.work_order}/`)).data,
    retry: false
  });
  const steps: Record<
    string,
    { target: string; action: string; label: string }
  > = {
    draft: { target: 'planned', action: 'plan', label: t`Plan linked Work` },
    planned: {
      target: 'ready',
      action: 'mark_ready',
      label: t`Check and mark Work ready`
    },
    ready: {
      target: 'in_progress',
      action: 'start',
      label: t`Start linked Work`
    },
    in_progress: {
      target: 'verifying',
      action: 'verify',
      label: t`Finish execution and begin verification`
    }
  };
  const step = work.data ? steps[work.data.lifecycle_status] : undefined;
  const endpoint = `${base}${step?.action === 'start' ? 'start' : 'work-transition'}/`;
  const readiness = useQuery<Readiness>({
    queryKey: [
      'packet-work-readiness',
      host,
      packet.pk,
      packet.status,
      work.data?.lifecycle_version,
      step?.action,
      user?.pk,
      generation
    ],
    enabled: Boolean(step),
    queryFn: async () =>
      (await api.get(endpoint, { params: { action: step?.action } })).data,
    retry: false
  });
  const refresh = async () => {
    await client.invalidateQueries({ queryKey: ['work-order'] });
    await work.refetch();
    if (step) await readiness.refetch();
    onChanged();
  };
  const advance = async () => {
    if (!work.data || (!step && !command.intent)) return;
    try {
      let intent = command.intent;
      if (!intent) {
        if (!step) return;
        if (typeof globalThis.crypto?.randomUUID !== 'function') {
          throw new Error(
            t`Secure command identifiers are unavailable. Use HTTPS or localhost; no command was sent.`
          );
        }
        intent = {
          url: endpoint,
          body: {
            expected_version: work.data.lifecycle_version,
            idempotency_key: globalThis.crypto.randomUUID(),
            ...(step.action === 'start' ? {} : { to_status: step.target })
          }
        };
      }
      setFeedback(null);
      const response = await command.execute<Record<string, unknown>>(
        intent,
        (result, retained) =>
          matchesPacketWorkReceipt(
            result,
            retained,
            packet.pk,
            packet.work_order!
          )
      );
      if (response) {
        const receipt = (
          intent.url.endsWith('/start/') ? response.result : response
        ) as Record<string, unknown>;
        const reference = work.data.reference;
        const status = String(receipt.lifecycle_status).replaceAll('_', ' ');
        const version = String(receipt.lifecycle_version);
        const eventId = String(receipt.event_id);
        setFeedback({
          boundary,
          message: t`${reference}: ${status} recorded · version ${version} · event #${eventId}`
        });
      }
    } catch (failure) {
      setFeedback({
        boundary,
        message:
          failure instanceof Error
            ? failure.message
            : t`Command unavailable. Reload the lifecycle before continuing.`,
        error: true
      });
    }
    await refreshSafely();
  };
  const refreshSafely = async () => {
    setRefreshIssue(null);
    try {
      await refresh();
    } catch {
      setRefreshIssue(boundary);
    }
  };
  const permitted =
    user?.is_superuser ||
    user?.permissions?.workorder?.includes(
      step?.action === 'start' ? 'execute' : 'transition'
    );
  const description =
    {
      verifying: t`Execution is finished. Open Work Closeout to complete the verification and service report. Independent report approval and separate final verification are still enforced.`,
      completed: t`Work is completed. Review the recorded report, audit trail and independent final verification in Work Closeout. Completion alone is not physical operating permission.`,
      canceled: t`Work is canceled. No lifecycle command is available here; review the Work record and its audit trail.`,
      on_hold: t`Work is on hold. Resolve the recorded hold through the authorized Work workflow before continuing.`
    }[work.data?.lifecycle_status ?? ''] ??
    t`The Repair Packet owns these commands. Safety, assignment, readiness and independent closeout approval remain required.`;
  const errorDetail = isAxiosError(readiness.error)
    ? readiness.error.response?.data?.detail
    : undefined;
  const disabled = lifecycleActionDisabled({
    permitted: Boolean(permitted),
    ready: Boolean(readiness.data?.ready),
    workVersion: work.data?.lifecycle_version,
    readinessVersion: readiness.data?.lifecycle_version,
    workError: work.isError,
    readinessError: readiness.isError,
    workFetching: work.isFetching,
    readinessFetching: readiness.isFetching,
    storageFailed: command.storageFailed,
    busy: command.busy
  });
  const visibleFeedback = feedback?.boundary === boundary ? feedback : null;
  const showCloseout = ['in_progress', 'verifying', 'completed'].includes(
    work.data?.lifecycle_status ?? ''
  );
  return (
    <Paper withBorder p='md' data-testid='packet-work-lifecycle'>
      <Stack gap='sm'>
        <Group align='flex-start'>
          <Title order={3} size='h5'>{t`Linked Work lifecycle`}</Title>
          {work.data && (
            <Badge variant='outline' tt='none'>
              {work.data.reference} ·{' '}
              {work.data.lifecycle_status.replaceAll('_', ' ')}
            </Badge>
          )}
        </Group>
        <Text size='sm'>{description}</Text>
        {work.isPending && (
          <Text
            size='sm'
            component='output'
          >{t`Reading linked Work and its current authority…`}</Text>
        )}
        {step && readiness.dataUpdatedAt > 0 && (
          <Text size='xs' c='dimmed'>
            {t`Readiness checked at:`}{' '}
            <time dateTime={new Date(readiness.dataUpdatedAt).toISOString()}>
              {new Date(readiness.dataUpdatedAt).toLocaleTimeString()}
            </time>
            {(work.isFetching || readiness.isFetching) &&
              ` · ${t`Refreshing…`}`}
          </Text>
        )}
        {(work.isError || readiness.isError) && (
          <Alert color='red' title={t`Readiness unavailable`}>
            {typeof errorDetail === 'string'
              ? errorDetail
              : t`Current Work readiness is unavailable. No further transition is available until readiness is restored. Any recorded command receipt remains valid.`}
          </Alert>
        )}
        {Boolean(readiness.data?.blockers.length) && (
          <Alert color='yellow' title={t`Blocked until:`}>
            <List size='sm'>
              {readiness.data?.blockers.map((blocker) => (
                <List.Item key={`${blocker.code}:${blocker.message}`}>
                  {blocker.message}
                </List.Item>
              ))}
            </List>
          </Alert>
        )}
        {step && !permitted && (
          <Alert color='yellow'>{t`Your account cannot perform this lifecycle action. An authorized Work operator must continue.`}</Alert>
        )}
        {step &&
          readiness.data &&
          readiness.data.lifecycle_version !== work.data?.lifecycle_version && (
            <Alert color='orange' title={t`Work version changed`}>
              {t`Readiness belongs to a different Work version. Reload the lifecycle and review its current state before continuing. No transition is confirmed.`}
            </Alert>
          )}
        {command.intent && (
          <Alert
            color='blue'
            title={t`Retained command — do not create a replacement`}
          >
            <Text size='sm'>
              {t`Target:`}{' '}
              {String(command.intent.body.to_status ?? 'in_progress')} ·{' '}
              {t`Expected version:`}{' '}
              {String(command.intent.body.expected_version)}
            </Text>
            <Text size='sm'>{t`Retry sends the exact saved request. If its outcome remains uncertain, ask an authorized operator to reconcile the Work event history and command receipt. Do not clear browser storage to force a new command.`}</Text>
          </Alert>
        )}
        {command.storageFailed && (
          <Alert
            color='red'
            title={t`Browser storage needs reconciliation`}
          >{t`Restore access to this browser's saved request, then reload with the same account. If the request cannot be recovered, an authorized operator must reconcile its event history before any replacement. No new command is permitted here.`}</Alert>
        )}
        {command.error && !command.storageFailed && (
          <Alert color='orange'>{command.error}</Alert>
        )}
        {refreshIssue === boundary && (
          <Alert color='orange'>{t`Latest Work state could not be refreshed. Any recorded command receipt remains valid. Reload the lifecycle before continuing.`}</Alert>
        )}
        {/* biome-ignore lint/a11y/useSemanticElements: This live region groups block-level alerts rather than a scalar form output. */}
        <div role='status' aria-live='polite' aria-atomic='true'>
          {visibleFeedback && (
            <Alert
              role='presentation'
              color={visibleFeedback.error ? 'red' : 'green'}
            >
              {visibleFeedback.message}
            </Alert>
          )}
        </div>
        <Group>
          {command.intent ? (
            <Button
              variant='default'
              className={classes.action}
              mih={44}
              loading={command.busy}
              disabled={command.storageFailed || !work.data || work.isError}
              onClick={() => void advance()}
            >{t`Retry retained lifecycle command`}</Button>
          ) : step ? (
            <Button
              w={{ base: '100%', sm: 'auto' }}
              className={classes.action}
              mih={44}
              styles={{ label: { whiteSpace: 'normal' } }}
              loading={command.busy || work.isPending || readiness.isPending}
              disabled={disabled}
              onClick={() => void advance()}
            >
              {step.label}
            </Button>
          ) : null}
          <Button
            variant='default'
            className={classes.action}
            mih={44}
            disabled={command.busy}
            onClick={() => void refreshSafely()}
          >{t`Reload lifecycle`}</Button>
          {showCloseout && (
            <Button
              variant='subtle'
              className={classes.action}
              mih={44}
              component={Link}
              to={`/maintenance/work-orders/${packet.work_order}/?work_panel=closeout`}
            >{t`Open Work Closeout`}</Button>
          )}
        </Group>
      </Stack>
    </Paper>
  );
}
