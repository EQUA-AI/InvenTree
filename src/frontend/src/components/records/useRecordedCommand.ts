import { t } from '@lingui/core/macro';
import { type QueryKey, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { useEffect, useRef, useState } from 'react';
import { api } from '../../App';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';

type Intent = {
  operation: string;
  fields: Record<string, unknown>;
  expected_version: number;
  idempotency_key: string;
};
/** Adopt an older unscoped Work intent only into its matching command family. */
function readStoredIntent(storage: string, legacy: string, scope: string) {
  const existing = localStorage.getItem(storage);
  if (existing || !['time', 'downtime'].includes(scope)) return existing;
  const raw = localStorage.getItem(legacy);
  if (!raw) return null;
  const intent: Intent = JSON.parse(raw);
  const matches =
    scope === 'downtime'
      ? intent.operation === 'downtime'
      : ['start', 'pause', 'stop', 'correct'].includes(intent.operation);
  if (!matches) return null;
  localStorage.setItem(storage, raw);
  if (localStorage.getItem(storage) !== raw)
    throw new Error('Storage unavailable');
  if (localStorage.getItem(legacy) === raw) localStorage.removeItem(legacy);
  return raw;
}
/** Retain exact intent before sending; an uncertain retry never mints a new key. */
export function useRecordedCommand<T>(
  endpoint: string,
  onSaved: (result: T) => void,
  refreshKeys: QueryKey[] = [],
  recordScope = ''
) {
  const host = useLocalState((s) => s.hostKey);
  const actor = useUserState((s) => s.user?.pk);
  const legacyStorage = `aimms-record-command:v1:${host}:${actor}:${endpoint}`;
  const storage = `${legacyStorage}${recordScope ? `:${recordScope}` : ''}`;
  const [retained, setRetained] = useState<Intent | null>(() => {
    try {
      return JSON.parse(
        readStoredIntent(storage, legacyStorage, recordScope) ?? 'null'
      );
    } catch {
      return null;
    }
  });
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState('');
  const active = useRef(false);
  const keys = useRef(refreshKeys);
  keys.current = refreshKeys;
  const query = useQueryClient();
  const currentScope = useRef(storage);
  currentScope.current = storage;
  useEffect(() => {
    active.current = false;
    setPending(false);
    setNotice('');
    try {
      const existing = readStoredIntent(storage, legacyStorage, recordScope);
      setRetained(JSON.parse(existing ?? 'null'));
      if (existing)
        setNotice(
          t`An original operation is pending in this browser. Resolve it before recording another.`
        );
    } catch {
      setRetained(null);
    }
    const observe = (event: StorageEvent) => {
      if (event.key !== storage) return;
      try {
        setRetained(JSON.parse(event.newValue ?? 'null'));
      } catch {
        return;
      }
      setNotice(
        event.newValue
          ? t`An original operation is pending in this browser. Resolve it before recording another.`
          : t`The original operation was resolved in another tab. The source is refreshing.`
      );
      if (!event.newValue)
        for (const queryKey of keys.current)
          void query.invalidateQueries({ queryKey });
    };
    window.addEventListener('storage', observe);
    return () => window.removeEventListener('storage', observe);
  }, [storage, legacyStorage, recordScope, query]);
  async function send(intent: Intent, reconcileOnly = false) {
    if (active.current) return;
    active.current = true;
    setPending(true);
    setNotice(t`Saving record…`);
    try {
      localStorage.setItem(storage, JSON.stringify(intent));
      if (localStorage.getItem(storage) !== JSON.stringify(intent))
        throw new Error('Storage unavailable');
    } catch {
      setNotice(
        t`Command could not be retained. Recording is unavailable in this browser.`
      );
      active.current = false;
      setPending(false);
      return;
    }
    setRetained(intent);
    try {
      const result = (
        await api.post<T>(
          endpoint,
          intent,
          reconcileOnly
            ? { headers: { 'X-AIMMS-Reconcile-Only': '1' } }
            : undefined
        )
      ).data;
      if (currentScope.current !== storage) return;
      if (
        typeof result === 'object' &&
        result !== null &&
        'record_command_outcome' in result &&
        result.record_command_outcome === 'not_committed'
      ) {
        if (
          !('idempotency_key' in result) ||
          result.idempotency_key !== intent.idempotency_key
        )
          throw new Error('Mismatched operation resolution');
        setNotice(
          t`Original operation was not applied. Refresh the source before starting a new operation.`
        );
      } else {
        if (
          typeof result !== 'object' ||
          result === null ||
          'record_command_outcome' in result ||
          !('version' in result) ||
          typeof result.version !== 'number' ||
          !Number.isSafeInteger(result.version) ||
          result.version < 1
        )
          throw new Error('Unconfirmed record response');
        setNotice(t`Record saved.`);
        onSaved(result);
      }
      localStorage.removeItem(storage);
      setRetained(null);
      for (const queryKey of refreshKeys)
        await query.invalidateQueries({ queryKey });
    } catch (error) {
      if (currentScope.current !== storage) return;
      const status = isAxiosError(error) ? error.response?.status : undefined;
      if (
        !reconcileOnly &&
        status &&
        status >= 400 &&
        status < 500 &&
        ![408, 429].includes(status)
      ) {
        localStorage.removeItem(storage);
        setRetained(null);
        const detail = isAxiosError(error)
          ? error.response?.data?.detail
          : null;
        setNotice(
          typeof detail === 'string'
            ? detail
            : t`Record refused. Refresh the source and review before trying again.`
        );
        for (const queryKey of refreshKeys)
          await query.invalidateQueries({ queryKey });
      } else {
        setRetained(intent);
        setNotice(
          t`Original outcome remains unconfirmed. This refusal or failure does not establish whether the earlier operation was saved. Retry the original operation when access is available.`
        );
      }
    } finally {
      if (currentScope.current === storage) {
        active.current = false;
        setPending(false);
      }
    }
  }
  return {
    pending,
    retained,
    notice,
    submit: (
      operation: string,
      fields: Record<string, unknown>,
      expected_version: number
    ) => {
      if (retained || active.current) return;
      try {
        const existing = readStoredIntent(storage, legacyStorage, recordScope);
        if (existing) {
          setRetained(JSON.parse(existing));
          setNotice(
            t`Outcome unconfirmed. Retry the retained operation to retrieve its saved result.`
          );
          return;
        }
      } catch {
        setNotice(
          t`Command could not be retained. Recording is unavailable in this browser.`
        );
        return;
      }
      return send({
        operation,
        fields,
        expected_version,
        idempotency_key: `${actor}:${crypto.randomUUID()}`
      });
    },
    retry: () => retained && send(retained, true)
  };
}
