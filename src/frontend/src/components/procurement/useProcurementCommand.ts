import { t } from '@lingui/core/macro';
import { useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { useEffect, useRef, useState } from 'react';
import { api } from '../../App';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import { commandResult } from './procurement';

type Intent = { endpoint: string; kind: string; body: Record<string, unknown> };

/** useRecordedCommand's persist-before-POST pattern, adapted to R8 envelopes.
 * Exact POST replay is the recovery protocol; these endpoints have no
 * record_command_outcome/version envelope or reconcile-only header.
 */
export function useProcurementCommand(
  scope: string,
  onSaved: (result: Record<string, unknown>) => void
) {
  const actor = useUserState((state) => state.user?.pk);
  const host = useLocalState((state) => state.getHost());
  const storage = `aimms:r8:command:${host}:${actor}:${scope}`;
  const query = useQueryClient();
  const [retained, setRetained] = useState<Intent | null>(null);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState('');
  const active = useRef(false);
  const boundary = useRef(storage);
  boundary.current = storage;
  useEffect(() => {
    active.current = false;
    setPending(false);
    setNotice('');
    const read = () => {
      try {
        const saved = localStorage.getItem(storage);
        setRetained(saved ? JSON.parse(saved) : null);
        if (saved)
          setNotice(t`Outcome unconfirmed. Retry the original operation.`);
      } catch {
        setNotice(t`Saved operation unavailable. Recording is blocked.`);
      }
    };
    read();
    const observe = (event: StorageEvent) => {
      if (event.key === storage) read();
    };
    window.addEventListener('storage', observe);
    return () => window.removeEventListener('storage', observe);
  }, [storage]);
  async function send(intent: Intent, replay: boolean) {
    if (active.current || !actor) return;
    const started = storage;
    active.current = true;
    setPending(true);
    try {
      const serialized = JSON.stringify(intent);
      localStorage.setItem(storage, serialized);
      if (localStorage.getItem(storage) !== serialized)
        throw new Error('Storage unavailable');
      setRetained(intent);
    } catch {
      setNotice(t`Operation could not be retained. Recording is blocked.`);
      active.current = false;
      setPending(false);
      return;
    }
    try {
      const result = (
        await api.post(intent.endpoint, intent.body, { timeout: 30000 })
      ).data;
      if (boundary.current !== started) return;
      if (!commandResult(intent.kind, intent.body, result))
        throw new Error('Unconfirmed response');
      onSaved(result);
      localStorage.removeItem(storage);
      setRetained(null);
      setNotice(t`Recorded`);
      await query.invalidateQueries({ queryKey: ['work-order-materials'] });
      await query.invalidateQueries({ queryKey: ['procurement-documents'] });
      await query.invalidateQueries({ queryKey: ['procurement-approval'] });
      window.dispatchEvent(new Event('aimms:proposals-refresh'));
    } catch (error) {
      if (boundary.current !== started) return;
      const status = isAxiosError(error) ? error.response?.status : undefined;
      if (!replay && status && [400, 403, 404, 409].includes(status)) {
        localStorage.removeItem(storage);
        setRetained(null);
        const detail = isAxiosError(error)
          ? error.response?.data?.detail
          : null;
        setNotice(
          typeof detail === 'string'
            ? detail
            : t`Recording refused. Refresh and review the source.`
        );
        await query.invalidateQueries({ queryKey: ['work-order-materials'] });
      } else setNotice(t`Outcome unconfirmed. Retry the original operation.`);
    } finally {
      if (boundary.current === started) {
        active.current = false;
        setPending(false);
      }
    }
  }
  return {
    retained,
    pending,
    notice,
    submit: (
      endpoint: string,
      kind: string,
      fields: Record<string, unknown>
    ) => {
      if (retained || active.current) return;
      try {
        const existing = localStorage.getItem(storage);
        if (existing) {
          setRetained(JSON.parse(existing));
          setNotice(t`Outcome unconfirmed. Retry the original operation.`);
          return;
        }
      } catch {
        setNotice(t`Saved operation unavailable. Recording is blocked.`);
        return;
      }
      const key = crypto.randomUUID();
      const body =
        kind === 'approval'
          ? {
              ...fields,
              agent_run_id: key,
              tool_call_id: key,
              agent_checkpoint_id: key
            }
          : kind === 'link'
            ? fields
            : { ...fields, operation_key: key };
      return send({ endpoint, kind, body }, false);
    },
    retry: () => retained && send(retained, true)
  };
}
