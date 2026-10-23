import { api } from '../../App';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import type { ProcurementIntent } from '../records/procurementRecovery';
import { useProcurementRecovery } from '../records/useProcurementRecovery';
import { commandResult } from './procurement';

type Intent = { endpoint: string; kind: string; body: Record<string, unknown> };

/** Native R8 envelopes share C's recovery boundary, retaining A's existing keys. */
export function useProcurementCommand(
  scope: string,
  onSaved: (result: Record<string, unknown>) => void
) {
  const actor = useUserState((state) => state.user?.pk);
  const host = useLocalState((state) => state.getHost());
  const command = useProcurementRecovery<Intent, Record<string, unknown>>({
    kind: 'procurement-command',
    scope,
    persistence: {
      key: `aimms:r8:command:${host}:${actor}:${scope}`,
      encode: (intent) => JSON.stringify(intent.request),
      decode: (raw): ProcurementIntent<Intent> | null => {
        if (raw === null) return null;
        const request = JSON.parse(raw);
        if (
          !request ||
          typeof request.endpoint !== 'string' ||
          !request.endpoint.startsWith('/api/') ||
          !['source', 'ack', 'document', 'approval', 'link'].includes(
            request.kind
          ) ||
          !request.body ||
          typeof request.body !== 'object' ||
          Array.isArray(request.body)
        )
          throw new Error('Invalid retained procurement command');
        return { schema: 1, kind: 'procurement-command', scope, request };
      }
    },
    execute: async (intent) =>
      (await api.post(intent.endpoint, intent.body, { timeout: 30000 })).data,
    accept: (result, intent): result is Record<string, unknown> => {
      if (!commandResult(intent.kind, intent.body, result)) return false;
      if (intent.kind !== 'document') return true;
      const row = result as Record<string, unknown>;
      return (
        row.attachment_state === 'verified' &&
        intent.endpoint === `/api/order/po/${row.order_id}/recorded-evidence/`
      );
    },
    onSaved: (result) => {
      onSaved(result);
      window.dispatchEvent(new Event('aimms:proposals-refresh'));
    },
    refreshKeys: [
      ['work-order-materials'],
      ['procurement-documents'],
      ['procurement-approval'],
      ['instance'],
      ['tabledata'],
      ['closeout-part-usage']
    ]
  });
  return {
    ...command,
    retained: command.retained?.request ?? null,
    submit: (
      endpoint: string,
      kind: string,
      fields: Record<string, unknown>
    ) => {
      if (command.blocked || command.pending || command.retained) return;
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
      return command.submit({ endpoint, kind, body });
    }
  };
}
