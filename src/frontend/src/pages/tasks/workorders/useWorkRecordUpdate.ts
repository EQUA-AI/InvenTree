import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { WorkOrderEvent } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { notifications } from '@mantine/notifications';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useApi } from '../../../contexts/ApiContext';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import {
  type UpdateIntent,
  type UpdateState,
  matchingReceipt,
  restoreUpdate,
  updateFailure
} from './workRecordUpdate';

/** One final action for standalone Closeout, including exact-operation recovery. */
export function useWorkRecordUpdate(
  workOrderId: number,
  invalidate: () => void
) {
  const api = useApi();
  const queryClient = useQueryClient();
  const userId = useUserState((s) => s.user?.pk);
  const generation = useUserState((s) => s.authGeneration);
  const host = useLocalState((s) => s.getHost());
  const storageKey = `aimms-closeout-operation:${JSON.stringify([host, userId, workOrderId])}`;
  const boundary = `${storageKey}:${generation}`;
  const activeBoundary = useRef(boundary);
  activeBoundary.current = boundary;
  const read = useCallback(() => {
    try {
      return restoreUpdate(localStorage.getItem(storageKey), workOrderId);
    } catch {
      return { status: 'idle' } as UpdateState;
    }
  }, [storageKey, workOrderId]);
  const [state, setState] = useState<UpdateState>(read);
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
    async (newIntent?: UpdateIntent) => {
      if (inFlight.current || !userId) return;
      inFlight.current = true;
      const startedAtBoundary = boundary;
      const current = () =>
        activeBoundary.current === startedAtBoundary &&
        useUserState.getState().user?.pk === userId &&
        useUserState.getState().authGeneration === generation &&
        useLocalState.getState().getHost() === host;
      const publish = (value: UpdateState): boolean => {
        if (!current()) return false;
        try {
          localStorage.setItem(storageKey, JSON.stringify(value));
        } catch {
          return false;
        }
        setState(value);
        return true;
      };
      const run = async () => {
        const stored = read();
        if (stored.status === 'confirmed' || stored.status === 'stale') {
          if (current()) setState(stored);
          return;
        }
        // Existing intent wins across rerenders, refreshes and tabs. A new form UUID
        // can never replace an unresolved operation.
        const intent = stored.status === 'idle' ? newIntent : stored.intent;
        if (!intent || intent.workOrderId !== workOrderId) return;
        if (
          !publish({
            status: 'pending',
            intent,
            reconciling: stored.status === 'uncertain'
          })
        ) {
          if (current())
            setState({
              status: 'failed',
              intent,
              message: t`The operation could not be retained in this browser. No update was sent.`
            });
          return;
        }
        try {
          const response = await api.post(
            apiUrl(ApiEndpoints.work_order_complete, workOrderId),
            intent.payload
          );
          if (!current()) return;
          if (
            response.status !== 200 ||
            !matchingReceipt(response.data, intent)
          ) {
            publish({
              status: 'uncertain',
              intent,
              message: t`The server has not confirmed this update. Check the original operation before retrying.`
            });
            return;
          }
          const receipt = response.data;
          // Time is read from the original audit event, never the browser clock.
          let recordedAt: string | null = null;
          try {
            const events = await api.get(
              apiUrl(ApiEndpoints.work_order_events, workOrderId)
            );
            const rows: WorkOrderEvent[] = Array.isArray(events.data)
              ? events.data
              : (events.data.results ?? []);
            const event = rows.find(
              (row) =>
                row.id === receipt.event_id &&
                row.event_type === 'COMPLETED' &&
                row.idempotency_key === intent.payload.idempotency_key
            );
            recordedAt = event?.created_at ?? null;
          } catch {
            /* The matching command receipt still confirms the internal update. */
          }
          if (publish({ status: 'confirmed', intent, receipt, recordedAt })) {
            invalidate();
            queryClient.invalidateQueries({
              queryKey: ['work-order', workOrderId]
            });
            notifications.show({
              message: t`Maintenance record updated`,
              color: 'green'
            });
          }
        } catch (error: any) {
          if (!current()) return;
          const classification = updateFailure(
            error?.response?.status,
            error?.response?.data?.code
          );
          // A refusal on reconciliation cannot disprove the earlier commit.
          const recovering =
            stored.status === 'uncertain' || stored.status === 'pending';
          const status = recovering ? 'uncertain' : classification;
          publish({
            status,
            intent,
            message:
              status === 'uncertain'
                ? t`The original update remains unconfirmed. Check the same operation again; a new update is locked.`
                : status === 'stale'
                  ? t`This record changed. Review the latest record and compare it with the retained draft.`
                  : t`The update was refused. Your account is retained. Review readiness and permissions before retrying.`
          });
          invalidate();
        }
      };
      try {
        // Same-origin tabs serialize the whole logical action. If a browser cannot
        // provide this coordination, it must not issue an uncoordinated command.
        if (!navigator.locks) {
          setState({ status: 'idle' });
          return;
        }
        await navigator.locks.request(storageKey, run);
      } finally {
        if (current()) inFlight.current = false;
      }
    },
    [
      api,
      boundary,
      generation,
      host,
      invalidate,
      queryClient,
      read,
      storageKey,
      userId,
      workOrderId
    ]
  );

  const reviewLatest = useCallback(async () => {
    // Only a confirmed refusal/stale result can be replaced. Uncertain stays locked.
    if (state.status !== 'stale' && state.status !== 'failed') return;
    invalidate();
    await queryClient.refetchQueries({ queryKey: ['work-order', workOrderId] });
    return queryClient.getQueryData<{ lifecycle_version: number }>([
      'work-order',
      workOrderId
    ])?.lifecycle_version;
  }, [state.status, invalidate, queryClient, workOrderId]);
  const releaseRefused = useCallback(async () => {
    if (
      (state.status !== 'stale' && state.status !== 'failed') ||
      !navigator.locks
    )
      return false;
    return navigator.locks.request(storageKey, () => {
      const stored = read();
      if (
        (stored.status !== 'stale' && stored.status !== 'failed') ||
        stored.intent.payload.idempotency_key !==
          state.intent.payload.idempotency_key ||
        activeBoundary.current !== boundary
      )
        return false;
      try {
        localStorage.removeItem(storageKey);
        setState({ status: 'idle' });
        return true;
      } catch {
        return false;
      }
    });
  }, [state, storageKey, read, boundary]);
  return {
    state,
    execute,
    reviewLatest,
    releaseRefused,
    available: !!userId && typeof navigator !== 'undefined' && !!navigator.locks
  };
}
