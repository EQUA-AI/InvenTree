import { t } from '@lingui/core/macro';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useApi } from '../../../contexts/ApiContext';
import { useLocalState } from '../../../states/LocalState';
import { useUserState } from '../../../states/UserState';
import { updateFailure } from './workRecordUpdate';

import {
  type CaptureSaveIntent as Intent,
  type CaptureSaveState as State,
  captureReceipt,
  restoreCaptureSave
} from './captureSaveState';

/** Capture saves retain one exact native command until its outcome is known. */
export function useCaptureSave(workOrderId: number, invalidate: () => void) {
  const api = useApi();
  const actor = useUserState((s) => s.user?.pk);
  const generation = useUserState((s) => s.authGeneration);
  const host = useLocalState((s) => s.getHost());
  const key = `aimms-capture-save:${JSON.stringify([host, actor, workOrderId])}`;
  const read = useCallback(() => {
    try {
      return restoreCaptureSave(sessionStorage.getItem(key), workOrderId);
    } catch {
      return null;
    }
  }, [key, workOrderId]);
  const [state, setState] = useState<State>(read);
  const running = useRef(false);
  const owner = useRef(key);
  useEffect(() => {
    owner.current = key;
    running.current = false;
    setState(read());
  }, [key, generation, read]);
  const publish = (next: State): boolean => {
    try {
      if (next) sessionStorage.setItem(key, JSON.stringify(next));
      else sessionStorage.removeItem(key);
      setState(next);
      return true;
    } catch {
      if (next) setState({ status: 'failed', intent: next.intent });
      return false;
    }
  };
  const save = async (next?: Intent) => {
    if (
      running.current ||
      !actor ||
      owner.current !== key ||
      state?.status === 'stale' ||
      state?.status === 'pending'
    )
      return;
    const intent = state?.intent ?? next;
    if (!intent) return;
    running.current = true;
    const current = () =>
      useUserState.getState().user?.pk === actor &&
      useUserState.getState().authGeneration === generation &&
      useLocalState.getState().getHost() === host;
    try {
      if (!publish({ status: 'pending', intent })) return;
      const response = await api.post(intent.url, intent.payload);
      if (!current()) return;
      if (
        ![200, 201].includes(response.status) ||
        !captureReceipt(response.data, intent, workOrderId)
      ) {
        publish({ status: 'uncertain', intent });
        return;
      }
      publish(null);
      invalidate();
    } catch (error: any) {
      if (!current()) return;
      const status =
        state?.status === 'uncertain'
          ? 'uncertain'
          : error?.response?.data?.code === 'CAPTURE_STALE_REVISION'
            ? 'stale'
            : updateFailure(
                error?.response?.status,
                error?.response?.data?.code
              );
      publish({ status, intent });
    } finally {
      if (current()) running.current = false;
    }
  };
  return {
    state,
    save,
    release: () => {
      if (
        owner.current !== key ||
        useUserState.getState().user?.pk !== actor ||
        useUserState.getState().authGeneration !== generation ||
        useLocalState.getState().getHost() !== host
      )
        return;
      if (state?.status === 'stale' || state?.status === 'failed')
        publish(null);
    },
    message:
      state?.status === 'uncertain'
        ? t`Save not confirmed. Your draft is retained. Check the original save before making another change.`
        : state?.status === 'stale'
          ? t`The record changed. Compare your draft with the latest authorized record before saving.`
          : t`Save refused. Your draft is retained. Check access and required fields before retrying.`
  };
}
