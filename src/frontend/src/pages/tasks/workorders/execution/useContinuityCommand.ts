import { t } from '@lingui/core/macro';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useApi } from '../../../../contexts/ApiContext';
import { useLocalState } from '../../../../states/LocalState';
import { useUserState } from '../../../../states/UserState';
import { updateFailure } from '../workRecordUpdate';
import {
  type ContinuityCommandState,
  type ContinuityIntent,
  matchingContinuityReceipt,
  restoreContinuityCommand
} from './continuityCommand';

/** Existing scoped Hold/Resume commands, with one retained operation on uncertainty. */
export function useContinuityCommand(workOrderId: number) {
  const api = useApi();
  const queryClient = useQueryClient();
  const userId = useUserState((state) => state.user?.pk);
  const generation = useUserState((state) => state.authGeneration);
  const host = useLocalState((state) => state.getHost());
  const storageKey = `aimms-work-continuity:${JSON.stringify([host, userId, workOrderId])}`;
  const boundary = `${storageKey}:${generation}`;
  const active = useRef(boundary);
  active.current = boundary;
  const read = useCallback((): ContinuityCommandState => {
    try {
      return restoreContinuityCommand(
        localStorage.getItem(storageKey),
        workOrderId
      );
    } catch {
      return { status: 'blocked' };
    }
  }, [storageKey, workOrderId]);
  const [state, setState] = useState<ContinuityCommandState>(read);
  const inFlight = useRef(false);
  useEffect(() => {
    setState(read());
    inFlight.current = false;
  }, [read, boundary]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === storageKey) setState(read());
    };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, [storageKey, read]);

  const execute = useCallback(
    async (newIntent?: ContinuityIntent) => {
      if (inFlight.current || !userId || !navigator.locks) return;
      inFlight.current = true;
      const started = boundary;
      const current = () =>
        active.current === started &&
        useUserState.getState().user?.pk === userId &&
        useUserState.getState().authGeneration === generation &&
        useLocalState.getState().getHost() === host;
      const publish = (value: ContinuityCommandState) => {
        if (!current()) return false;
        try {
          localStorage.setItem(storageKey, JSON.stringify(value));
        } catch {
          setState({ status: 'blocked' });
          return false;
        }
        setState(value);
        return true;
      };
      try {
        await navigator.locks.request(storageKey, async () => {
          if (!current()) return;
          const stored = read();
          if (['blocked', 'failed', 'stale'].includes(stored.status)) {
            setState(stored);
            return;
          }
          const recovering =
            stored.status === 'pending' || stored.status === 'uncertain';
          const intent = recovering ? stored.intent : newIntent;
          if (!intent) {
            if (stored.status === 'confirmed') setState(stored);
            return;
          }
          if (intent.workOrderId !== workOrderId) return;
          if (
            stored.status === 'confirmed' &&
            intent.payload.expected_version < stored.receipt.lifecycle_version
          ) {
            setState(stored);
            return;
          }
          if (!publish({ status: 'pending', intent })) return;
          try {
            const response = await api.post(
              `/api/tasks/work-orders/${workOrderId}/${intent.action}/`,
              intent.payload
            );
            if (!current()) return;
            if (
              response.status === 200 &&
              matchingContinuityReceipt(response.data, intent)
            )
              publish({ status: 'confirmed', intent, receipt: response.data });
            else
              publish({
                status: 'uncertain',
                intent,
                message: t`The server has not confirmed this operation. A new hold or resume is locked until the original result is checked.`
              });
          } catch (error: unknown) {
            if (!current()) return;
            const failure = error as {
              response?: {
                status?: number;
                data?: {
                  code?: string;
                  detail?: string;
                  blockers?: { message?: unknown }[];
                };
              };
            };
            const status = recovering
              ? 'uncertain'
              : updateFailure(
                  failure.response?.status,
                  failure.response?.data?.code
                );
            const data = failure.response?.data;
            const blockers = Array.isArray(data?.blockers)
              ? data.blockers.flatMap((blocker) =>
                  typeof blocker?.message === 'string' ? [blocker.message] : []
                )
              : [];
            const refusal = [data?.detail, ...blockers]
              .filter(
                (text): text is string =>
                  typeof text === 'string' && text.length > 0
              )
              .join(' · ');
            publish({
              status,
              intent,
              message:
                status === 'uncertain'
                  ? t`The original operation remains unconfirmed. Check the same operation again; a refusal now cannot disprove an earlier save.`
                  : status === 'stale'
                    ? t`This Work changed. Review the latest saved Work before preparing another command.`
                    : refusal ||
                      t`The server refused this operation. Review the latest Work and its requirements.`
            });
          }
          if (current())
            await queryClient.invalidateQueries({
              queryKey: ['work-order', workOrderId]
            });
        });
      } catch {
        if (current()) setState(read());
      } finally {
        if (current()) inFlight.current = false;
      }
    },
    [
      api,
      boundary,
      generation,
      host,
      queryClient,
      read,
      storageKey,
      userId,
      workOrderId
    ]
  );

  const reviewRefusal = useCallback(async () => {
    if (!navigator.locks || !['failed', 'stale'].includes(state.status)) return;
    await navigator.locks.request(storageKey, async () => {
      const stored = read();
      if (
        (stored.status !== 'failed' && stored.status !== 'stale') ||
        !('intent' in state) ||
        stored.intent.payload.idempotency_key !==
          state.intent.payload.idempotency_key ||
        active.current !== boundary
      )
        return;
      try {
        await queryClient.refetchQueries(
          { queryKey: ['work-order', workOrderId] },
          { throwOnError: true }
        );
        const fresh = queryClient.getQueryData<{
          id: number;
          lifecycle_version: number;
        }>(['work-order', workOrderId]);
        if (
          active.current !== boundary ||
          fresh?.id !== workOrderId ||
          !Number.isSafeInteger(fresh.lifecycle_version)
        )
          return;
        localStorage.removeItem(storageKey);
        setState({ status: 'idle' });
      } catch {
        /* Keep the refused intent until a fresh authorized read succeeds. */
      }
    });
  }, [boundary, queryClient, read, state, storageKey, workOrderId]);
  return {
    state,
    execute,
    reviewRefusal,
    available: !!userId && !!navigator.locks
  };
}
