import { t } from '@lingui/core/macro';
import { isAxiosError } from 'axios';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useApi } from '../contexts/ApiContext';
import { useLocalState } from '../states/LocalState';
import { useUserState } from '../states/UserState';

export interface RetainedCommand {
  url: string;
  body: Record<string, unknown>;
  context?: Record<string, string | null>;
}

export function useRetainedCommand(resource: string, allowed: RegExp) {
  const api = useApi();
  const host = useLocalState((state) => state.hostKey);
  const actor = useUserState((state) => state.user?.pk);
  const generation = useUserState((state) => state.authGeneration);
  const storageKey = JSON.stringify(['aimms-command', host, actor, resource]);
  const boundary = `${storageKey}:${generation}`;
  const active = useRef(boundary);
  active.current = boundary;
  const inFlight = useRef(false);
  const [intent, setIntent] = useState<RetainedCommand | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{
    status: number;
    data: Record<string, unknown>;
  } | null>(null);
  const [storageFailed, setStorageFailed] = useState(false);
  const pattern = allowed.source;
  const read = useCallback((): RetainedCommand | null => {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const retained = JSON.parse(raw) as RetainedCommand;
    if (
      !new RegExp(pattern).test(retained.url) ||
      !retained.body ||
      typeof retained.body.idempotency_key !== 'string'
    )
      throw new Error('Invalid retained request');
    return retained;
  }, [storageKey, pattern]);
  useEffect(() => {
    const restore = () => {
      try {
        setIntent(read());
        setStorageFailed(false);
      } catch {
        setStorageFailed(true);
        setError(
          t`Retained request unavailable. Reconcile the previous request before another command.`
        );
      }
    };
    restore();
    setBusy(false);
    setRefusal(null);
    inFlight.current = false;
    const sync = (event: StorageEvent) => {
      if (event.key === storageKey) restore();
    };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, [boundary, storageKey, read]);
  const execute = async <Result>(
    request: RetainedCommand,
    matches: (data: Result, retained: RetainedCommand) => boolean
  ): Promise<Result | undefined> => {
    if (inFlight.current || !actor || storageFailed) return;
    inFlight.current = true;
    const started = boundary;
    const current = () =>
      active.current === started &&
      useUserState.getState().authGeneration === generation &&
      useUserState.getState().user?.pk === actor &&
      useLocalState.getState().hostKey === host;
    setBusy(true);
    setError(null);
    setRefusal(null);
    try {
      let retained: RetainedCommand;
      try {
        retained = read() ?? request;
        if (!new RegExp(pattern).test(retained.url))
          throw new Error('Invalid command resource');
        localStorage.setItem(storageKey, JSON.stringify(retained));
        setIntent(retained);
      } catch {
        setStorageFailed(true);
        throw new Error('Unable to retain request safely');
      }
      const { data } = await api.post<Result>(retained.url, retained.body);
      if (!current()) return;
      if (!matches(data, retained))
        throw new Error('Receipt does not match retained command');
      localStorage.removeItem(storageKey);
      setIntent(null);
      return data;
    } catch (failure) {
      if (!current()) return;
      if (
        isAxiosError(failure) &&
        failure.response &&
        failure.response.status >= 400 &&
        failure.response.status < 500 &&
        ![408, 429].includes(failure.response.status)
      ) {
        try {
          localStorage.removeItem(storageKey);
          setIntent(null);
        } catch {
          setStorageFailed(true);
        }
        const detail = failure.response.data?.detail;
        setRefusal({
          status: failure.response.status,
          data: failure.response.data ?? {}
        });
        setError(
          typeof detail === 'string'
            ? detail
            : t`Command refused. Reload and review your current authority and report.`
        );
      } else
        setError(
          t`Command outcome not confirmed. Retry the retained request; no new request will replace it.`
        );
    } finally {
      if (current()) {
        inFlight.current = false;
        setBusy(false);
      }
    }
  };
  return { intent, busy, error, refusal, storageFailed, execute };
}
