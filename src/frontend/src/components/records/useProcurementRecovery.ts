import { t } from '@lingui/core/macro';
import { type QueryKey, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api } from '../../App';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import {
  type MaintenanceReceiptRequest,
  type ProcurementIntent,
  type ReceiptStockSnapshot,
  canonicalIntent,
  isConsumedAllocation,
  isReceiptResponse,
  procurementRefreshKeys,
  readProcurementIntent
} from './procurementRecovery';

/** Exact native replay, scoped to host/person/entity; no reconcile-only header. */
export function useProcurementRecovery<Request, Result>({
  kind,
  scope,
  execute,
  accept,
  onSaved,
  refreshKeys = [],
  persistence,
  retainOnSaved = false
}: {
  kind: string;
  scope: string;
  execute: (request: Request, replay: boolean) => Promise<unknown>;
  accept: (value: unknown, request: Request) => value is Result;
  onSaved: (result: Result) => void;
  refreshKeys?: QueryKey[];
  /** Preserve native consumers' existing namespaces and exact retained bodies. */
  persistence?: {
    key: string;
    encode: (intent: ProcurementIntent<Request>) => string;
    decode: (raw: string | null) => ProcurementIntent<Request> | null;
  };
  retainOnSaved?: boolean;
}) {
  const host = useLocalState((s) => s.hostKey);
  const actor = useUserState((s) => s.user?.pk);
  const storage =
    persistence?.key ??
    `aimms-procurement:v1:${host}:${actor}:${kind}:${scope}`;
  const [retained, setRetained] = useState<ProcurementIntent<Request> | null>(
    null
  );
  const [pending, setPending] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [notice, setNotice] = useState('');
  const query = useQueryClient();
  const identity = useRef({ storage, generation: 0 });
  if (identity.current.storage !== storage)
    identity.current = { storage, generation: identity.current.generation + 1 };
  const active = useRef<string | null>(null);
  const mounted = useRef(true);
  const options = useRef({
    execute,
    accept,
    onSaved,
    refreshKeys,
    persistence,
    retainOnSaved
  });
  options.current = {
    execute,
    accept,
    onSaved,
    refreshKeys,
    persistence,
    retainOnSaved
  };

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      identity.current.generation += 1;
    };
  }, []);

  useEffect(() => {
    active.current = null;
    setPending(false);
    const read = () => {
      try {
        const raw = localStorage.getItem(storage);
        const intent = options.current.persistence
          ? options.current.persistence.decode(raw)
          : readProcurementIntent<Request>(raw, kind, scope);
        setRetained(intent);
        setBlocked(false);
        setNotice(
          intent ? t`Outcome unconfirmed. Recover the original operation.` : ''
        );
      } catch {
        setBlocked(true);
        setNotice(t`Retained operation unavailable. Recording is blocked.`);
      }
    };
    read();
    const observe = (event: StorageEvent) => {
      if (event.key !== storage && event.key !== null) return;
      read();
      for (const queryKey of options.current.refreshKeys)
        void query.invalidateQueries({ queryKey });
    };
    window.addEventListener('storage', observe);
    return () => window.removeEventListener('storage', observe);
  }, [storage, kind, scope, query]);

  async function send(intent: ProcurementIntent<Request>, replay = false) {
    if (
      active.current === storage ||
      !actor ||
      identity.current.storage !== storage
    )
      return;
    const generation = identity.current.generation;
    const current = () =>
      mounted.current &&
      identity.current.storage === storage &&
      identity.current.generation === generation;
    const call = options.current;
    let raw: string;
    let snapshot: ProcurementIntent<Request>;
    try {
      raw = call.persistence
        ? call.persistence.encode(intent)
        : canonicalIntent(intent);
      snapshot = call.persistence
        ? call.persistence.decode(raw)!
        : readProcurementIntent<Request>(raw, kind, scope)!;
      const existing = localStorage.getItem(storage);
      if (
        existing !== null &&
        canonicalIntent(
          call.persistence
            ? call.persistence.decode(existing)
            : readProcurementIntent<Request>(existing, kind, scope)
        ) !== canonicalIntent(snapshot)
      )
        throw new Error('A different operation is retained');
      localStorage.setItem(storage, raw);
      if (localStorage.getItem(storage) !== raw)
        throw new Error('Storage unavailable');
    } catch {
      setBlocked(true);
      setNotice(t`Retained operation unavailable. Recording is blocked.`);
      return;
    }
    active.current = storage;
    setRetained(snapshot);
    setPending(true);
    setNotice(t`Saving record…`);
    let confirmed = false;
    try {
      const result = await call.execute(snapshot.request, replay);
      if (!call.accept(result, snapshot.request))
        throw new Error('Unconfirmed response');
      // Ignore stale callbacks after navigation, logout or an A→B→A transition.
      if (!current()) return;
      setNotice(t`Record saved.`);
      // A failed refresh cannot undo a confirmed write. If a consumer fails to
      // retain its result, leave the exact intent available for safe readback.
      confirmed = true;
      call.onSaved(result);
      if (!call.retainOnSaved) {
        const latest = localStorage.getItem(storage);
        if (latest === raw) {
          localStorage.removeItem(storage);
          setRetained(null);
        } else {
          setRetained(
            call.persistence
              ? call.persistence.decode(latest)
              : readProcurementIntent<Request>(latest, kind, scope)
          );
        }
      }
    } catch {
      if (!current() || confirmed) return;
      setRetained(snapshot);
      setNotice(t`Outcome unconfirmed. Recover the original operation.`);
    } finally {
      // Scope changes leave the original intent available in its original namespace.
      if (current()) {
        active.current = null;
        setPending(false);
        for (const queryKey of call.refreshKeys)
          void query.invalidateQueries({ queryKey });
      }
    }
  }

  return {
    clearConfirmed: (request: Request) => {
      if (active.current === storage) return;
      try {
        const raw = localStorage.getItem(storage);
        const intent = options.current.persistence
          ? options.current.persistence.decode(raw)
          : readProcurementIntent<Request>(raw, kind, scope);
        if (
          intent &&
          canonicalIntent(intent.request) === canonicalIntent(request)
        ) {
          localStorage.removeItem(storage);
          setRetained(null);
        }
      } catch {
        setBlocked(true);
      }
    },
    pending,
    blocked,
    retained,
    notice,
    submit: async (request: Request) => {
      if (blocked || active.current === storage) return;
      try {
        const raw = localStorage.getItem(storage);
        const existing = options.current.persistence
          ? options.current.persistence.decode(raw)
          : readProcurementIntent<Request>(raw, kind, scope);
        if (existing) {
          setRetained(existing);
          setNotice(t`Outcome unconfirmed. Recover the original operation.`);
          return;
        }
      } catch {
        setBlocked(true);
        setNotice(t`Retained operation unavailable. Recording is blocked.`);
        return;
      }
      return send({ schema: 1, kind, scope, request });
    },
    retry: async () => {
      try {
        const raw = localStorage.getItem(storage);
        const original = options.current.persistence
          ? options.current.persistence.decode(raw)
          : readProcurementIntent<Request>(raw, kind, scope);
        if (original) return send(original, true);
      } catch {
        setBlocked(true);
        setNotice(t`Retained operation unavailable. Recording is blocked.`);
      }
    }
  };
}

/** Called by the native receiving form; the caller retains its exact batch/location/status. */
export function useMaintenanceReceiptRecovery({
  workOrderId,
  purchaseOrderId,
  onSaved
}: {
  workOrderId: number;
  purchaseOrderId: number;
  onSaved: (stocks: ReceiptStockSnapshot[]) => void;
}) {
  return useProcurementRecovery<
    MaintenanceReceiptRequest,
    ReceiptStockSnapshot[]
  >({
    kind: 'maintenance-receipt',
    scope: `work:${workOrderId}:po:${purchaseOrderId}`,
    execute: async (request) =>
      (await api.post(`/api/order/po/${purchaseOrderId}/receive/`, request))
        .data,
    accept: isReceiptResponse,
    onSaved,
    refreshKeys: procurementRefreshKeys(workOrderId, purchaseOrderId)
  });
}

/** Exact native document POST replay preserves PDF/MIME bytes and original recorder. */
export function useRecordedDocumentRecovery({
  workOrderId,
  purchaseOrderId,
  onSaved
}: {
  workOrderId: number;
  purchaseOrderId: number;
  onSaved: (result: Record<string, unknown>) => void;
}) {
  return useProcurementRecovery<
    Record<string, unknown>,
    Record<string, unknown>
  >({
    kind: 'recorded-document',
    scope: `work:${workOrderId}:po:${purchaseOrderId}`,
    execute: async (request) =>
      (
        await api.post(
          `/api/order/po/${purchaseOrderId}/recorded-evidence/`,
          request
        )
      ).data,
    accept: (value, request): value is Record<string, unknown> => {
      if (!value || typeof value !== 'object') return false;
      const row = value as Record<string, unknown>;
      return (
        row.operation_key === request.operation_key &&
        row.order_id === purchaseOrderId &&
        row.state === 'recorded_not_sent' &&
        row.supplier_email_sent === false &&
        row.attachment_state === 'verified'
      );
    },
    onSaved,
    refreshKeys: procurementRefreshKeys(workOrderId, purchaseOrderId)
  });
}

/** Consumption retries use the same allocation, whose native tracking guards replay. */
export function useAllocationUseRecovery({
  workOrderId,
  allocationId,
  onSaved
}: {
  workOrderId: number;
  allocationId: number;
  onSaved: (result: Record<string, unknown>) => void;
}) {
  return useProcurementRecovery<Record<string, never>, Record<string, unknown>>(
    {
      kind: 'allocation-use',
      scope: `work:${workOrderId}:allocation:${allocationId}`,
      execute: async (request, replay) =>
        (
          await api.post(
            `/api/tasks/work-orders/${workOrderId}/job-kit/allocations/${allocationId}/consume/`,
            request,
            replay ? { headers: { 'X-AIMMS-Reconcile-Only': '1' } } : undefined
          )
        ).data,
      accept: (value): value is Record<string, unknown> =>
        isConsumedAllocation(value, allocationId),
      onSaved,
      refreshKeys: procurementRefreshKeys(workOrderId)
    }
  );
}

/** Existing named screen approval only; retains the exact reviewed revision/hash. */
export function usePurchaseApprovalRecovery({
  workOrderId,
  approvalId,
  onSaved
}: {
  workOrderId?: number;
  approvalId: string;
  onSaved: (approval: Record<string, unknown>) => void;
}) {
  return useProcurementRecovery<
    {
      revision: number;
      review_hash: string;
      reason?: string;
      instructions?: string;
    },
    Record<string, unknown>
  >({
    kind: 'purchase-approval',
    scope: `work:${workOrderId ?? 'native'}:approval:${approvalId}`,
    execute: async (request) =>
      (
        await api.post(`/api/approvals/${approvalId}/approve/`, request, {
          timeout: 30000
        })
      ).data,
    accept: (value): value is Record<string, unknown> => {
      if (!value || typeof value !== 'object') return false;
      const row = value as Record<string, unknown>;
      return (
        row.id === approvalId &&
        row.action_type === 'purchase_order' &&
        ['succeeded', 'failed', 'denied', 'canceled', 'expired'].includes(
          String(row.status)
        )
      );
    },
    // Callback receives the native terminal outcome; failed is never presented as placed.
    onSaved,
    refreshKeys: [
      ...(workOrderId
        ? procurementRefreshKeys(workOrderId)
        : [['instance'], ['tabledata']]),
      ['procurement-approval', approvalId],
      ['approval-inbox'],
      ['approval-review', approvalId],
      ['approval-count']
    ]
  });
}

/** Issue recovery reads the same actor's retained attribution without another transition. */
export function useAllocationIssueRecovery({
  workOrderId,
  allocationId,
  onSaved
}: {
  workOrderId: number;
  allocationId: number;
  onSaved: (result: Record<string, unknown>) => void;
}) {
  const actor = useUserState((s) => s.user?.pk);
  return useProcurementRecovery<Record<string, never>, Record<string, unknown>>(
    {
      kind: 'allocation-issue',
      scope: `work:${workOrderId}:allocation:${allocationId}`,
      execute: async (request, replay) =>
        (
          await api.post(
            `/api/tasks/work-orders/${workOrderId}/job-kit/allocations/${allocationId}/issue/`,
            request,
            replay ? { headers: { 'X-AIMMS-Reconcile-Only': '1' } } : undefined
          )
        ).data,
      accept: (value): value is Record<string, unknown> => {
        const row = value as Record<string, unknown> | null;
        return (
          !!row &&
          row.id === allocationId &&
          row.issued_by === actor &&
          typeof row.issued_at === 'string'
        );
      },
      onSaved,
      refreshKeys: procurementRefreshKeys(workOrderId)
    }
  );
}
