import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useApi } from '../../../../contexts/ApiContext';
import { useLocalState } from '../../../../states/LocalState';
import { useUserState } from '../../../../states/UserState';
import {
  type ProcessIntent,
  type ProcessState,
  canReviewProcess,
  processReceipt,
  restoreProcess
} from './processCommand';

/** Actor/host-scoped durable intent, serialized across tabs; retry the same key. */
export function useProcessCommand(
  kind: ProcessIntent['kind'],
  workId: number,
  targetId: number
) {
  const api = useApi();
  const client = useQueryClient();
  const actor = useUserState((s) => s.user?.pk);
  const generation = useUserState((s) => s.authGeneration);
  const host = useLocalState((s) => s.getHost());
  const key = `aimms-process:${JSON.stringify([host, actor, kind, workId, targetId])}`;
  const boundary = `${key}:${generation}`;
  const active = useRef(boundary);
  active.current = boundary;
  const read = (): ProcessState => {
    try {
      return restoreProcess(localStorage.getItem(key), kind, workId, targetId);
    } catch {
      return {
        status: 'refused',
        intent: {
          kind,
          workId,
          targetId,
          url: '',
          payload: { idempotency_key: '' }
        },
        message: 'Browser storage unavailable; review saved Work.'
      };
    }
  };
  const [state, setState] = useState<ProcessState>(read);
  const busy = useRef(false);
  useEffect(() => {
    setState(read());
    busy.current = false;
    const sync = (event: StorageEvent) => {
      if (event.key === key) setState(read());
    };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, [boundary]);
  const execute = async (intent?: ProcessIntent) => {
    if (busy.current || !actor || !navigator.locks) return;
    busy.current = true;
    const started = boundary;
    const current = () =>
      active.current === started &&
      useUserState.getState().authGeneration === generation &&
      useUserState.getState().user?.pk === actor &&
      useLocalState.getState().getHost() === host;
    try {
      await navigator.locks.request(key, async () => {
        if (!current()) return;
        const saved = read();
        if (['confirmed', 'refused'].includes(saved.status)) {
          setState(saved);
          return;
        }
        const original = 'intent' in saved ? saved.intent : intent;
        if (!original) return;
        const publish = (value: ProcessState) => {
          if (!current()) return;
          localStorage.setItem(key, JSON.stringify(value));
          setState(value);
        };
        publish({ status: 'pending', intent: original });
        try {
          const response = await api.post(original.url, original.payload);
          publish({
            status: processReceipt(response.data, original)
              ? 'confirmed'
              : 'uncertain',
            intent: original,
            confirmedVersion:
              original.kind === 'escalation' &&
              processReceipt(response.data, original)
                ? response.data.version_after
                : undefined,
            result:
              (original.kind === 'recurrence' ||
                original.kind === 'routine_finding') &&
              processReceipt(response.data, original)
                ? {
                    outcome:
                      response.data.process_receipt.work_order_id ===
                      original.workId
                        ? 'reopened'
                        : 'linked_created',
                    workId: response.data.process_receipt.work_order_id,
                    reference: response.data.reference,
                    acceptance:
                      response.data.process_receipt.metadata?.closure_acceptance
                  }
                : undefined
          });
        } catch (error) {
          const failure = error as {
            response?: {
              status?: number;
              data?: { detail?: string; code?: string };
            };
          };
          const refused =
            (saved.status === 'idle' &&
              [400, 401, 403, 404, 409, 422].includes(
                failure.response?.status ?? 0
              )) ||
            // The routine source is locked; ledger replay precedes its version
            // check. Exact STALE_VERSION proves this intent never committed.
            (original.kind === 'routine_finding' &&
              failure.response?.status === 409 &&
              failure.response.data?.code === 'STALE_VERSION');
          publish({
            status: refused ? 'refused' : 'uncertain',
            intent: original,
            message: failure.response?.data?.detail
          });
        }
        if (current())
          await client.invalidateQueries({ queryKey: ['work-order', workId] });
      });
    } finally {
      if (current()) busy.current = false;
    }
  };
  const review = async () => {
    if (
      !navigator.locks ||
      !['confirmed', 'refused'].includes(state.status) ||
      !('intent' in state) ||
      !state.intent.payload.idempotency_key
    )
      return;
    const started = boundary;
    const current = () =>
      active.current === started &&
      useUserState.getState().authGeneration === generation &&
      useUserState.getState().user?.pk === actor &&
      useLocalState.getState().getHost() === host;
    await navigator.locks.request(key, async () => {
      if (!current()) return;
      const stored = read();
      if (!canReviewProcess(stored, state)) {
        setState(stored);
        return;
      }
      try {
        await client.refetchQueries(
          { queryKey: ['work-order', workId] },
          { throwOnError: true }
        );
        const fresh = client.getQueryData<{
          id: number;
          lifecycle_version: number;
        }>(['work-order', workId]);
        if (
          !current() ||
          fresh?.id !== workId ||
          !Number.isSafeInteger(fresh.lifecycle_version)
        )
          return;
        const latest = read();
        if (!canReviewProcess(latest, state)) {
          setState(latest);
          return;
        }
        localStorage.removeItem(key);
        setState({ status: 'idle' });
      } catch {
        /* A failed read keeps the saved operation. */
      }
    });
  };
  return { state, execute, review, available: !!actor && !!navigator.locks };
}
