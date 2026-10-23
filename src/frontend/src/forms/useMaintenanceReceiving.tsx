import { t } from '@lingui/core/macro';
import { Alert, Anchor, Button, Group, Stack, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../App';
import {
  type ReceiptStockSnapshot,
  isReceiptResponse,
  procurementRefreshKeys
} from '../components/records/procurementRecovery';
import { useProcurementRecovery } from '../components/records/useProcurementRecovery';
import { useLocalState } from '../states/LocalState';
import { useUserState } from '../states/UserState';
import {
  type MaintenanceReceiveRequest,
  type MaintenanceReceivingContext,
  readRetainedReceipt,
  retainReceipt,
  validateMaintenanceReceipt
} from './maintenanceReceiving';

/** Native receiving uses its array response; readback is a separate safe GET. */
export function useMaintenanceReceiving(endpoint: string, opened: boolean) {
  const host = useLocalState((s) => s.hostKey);
  const actor = useUserState((s) => s.user?.pk);
  const storageKey = `aimms-maintenance-receive:v1:${host}:${actor}:${endpoint}`;
  const submitted = useRef<{
    storage: string;
    generation: number;
    request: MaintenanceReceiveRequest;
  } | null>(null);
  const boundary = useRef({ storage: storageKey, generation: 0 });
  if (boundary.current.storage !== storageKey)
    boundary.current = {
      storage: storageKey,
      generation: boundary.current.generation + 1
    };
  const generation = boundary.current.generation;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      boundary.current.generation += 1;
    };
  }, []);
  const current = () =>
    mounted.current &&
    boundary.current.storage === storageKey &&
    boundary.current.generation === generation;
  const [intent, setIntent] = useState<MaintenanceReceiveRequest | null>(null);
  const [notice, setNotice] = useState('');
  const [storageFailed, setStorageFailed] = useState(false);
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ['maintenance-receiving', host, actor, endpoint],
    enabled: opened && !!actor,
    staleTime: 0,
    retry: false,
    queryFn: async () =>
      (await api.get<MaintenanceReceivingContext>(endpoint)).data
  });
  useEffect(() => {
    setNotice('');
    setStorageFailed(false);
    try {
      setIntent(readRetainedReceipt(localStorage.getItem(storageKey)));
    } catch {
      setStorageFailed(true);
      setNotice(t`Receipt recovery storage unavailable`);
    }
  }, [storageKey, opened]);
  const context = query.data;
  const recovery = useProcurementRecovery<
    MaintenanceReceiveRequest,
    ReceiptStockSnapshot[]
  >({
    kind: 'maintenance-receipt',
    scope: endpoint,
    persistence: {
      key: storageKey,
      encode: (operation) => JSON.stringify(operation.request),
      decode: (raw) => {
        const request = readRetainedReceipt(raw);
        return request
          ? { schema: 1, kind: 'maintenance-receipt', scope: endpoint, request }
          : null;
      }
    },
    retainOnSaved: true,
    execute: async (request) => (await api.post(endpoint, request)).data,
    accept: isReceiptResponse,
    onSaved: () => {
      void refresh();
    },
    refreshKeys: context?.binding
      ? procurementRefreshKeys(context.binding.work_order)
      : []
  });
  const busy = recovery.pending;
  const receipt = context?.receipts.find(
    (row) => row.operation_key === intent?.maintenance_operation_key
  );
  const pending = context?.receipts.find(
    (row) => row.reservation_state !== 'allocated'
  );
  const retained =
    intent ?? recovery.retained?.request ?? pending?.request ?? null;
  const disabled =
    !context ||
    query.isError ||
    query.isFetching ||
    storageFailed ||
    recovery.blocked ||
    busy ||
    !!retained;
  async function refresh() {
    if (!current()) return;
    await query.refetch();
    if (!current()) return;
    await queryClient.invalidateQueries({ queryKey: ['work-order'] });
    await queryClient.invalidateQueries({ queryKey: ['work-order-materials'] });
    await queryClient.invalidateQueries({ queryKey: ['closeout-part-usage'] });
    await queryClient.invalidateQueries({ queryKey: ['job-kit'] });
  }
  function prepare(
    data: Record<string, any>,
    form: { setError: (name: string, error: { message: string }) => void }
  ) {
    if (!current() || !context || query.isError) return null;
    if (!context.maintenance) return data;
    if (!context.binding || retained || storageFailed || recovery.blocked)
      return null;
    const request: MaintenanceReceiveRequest = {
      maintenance_operation_key: crypto.randomUUID(),
      maintenance_requirement_version: context.binding.requirement_version,
      location: data.location ?? null,
      items: data.items ?? []
    };
    const invalid = validateMaintenanceReceipt(
      request,
      context.binding.purchase_order_line
    );
    if (invalid) {
      const label =
        invalid === 'batch'
          ? t`Batch is required`
          : invalid === 'location'
            ? t`Location is required`
            : t`Receive one maintenance line`;
      form.setError(invalid === 'batch' ? 'items.0.batch_code' : 'location', {
        message: label
      });
      setNotice(label);
      return null;
    }
    try {
      const saved = retainReceipt(localStorage, storageKey, request);
      submitted.current = { storage: storageKey, generation, request: saved };
      setIntent(saved);
      return saved;
    } catch {
      setStorageFailed(true);
      setNotice(t`Receipt recovery storage unavailable`);
      return null;
    }
  }
  async function recover() {
    if (!retained || busy || recovery.blocked) return;
    try {
      if ((receipt ?? pending)?.actor && (receipt ?? pending)?.actor !== actor)
        throw new Error('Original receiving actor required');
      const saved = retainReceipt(localStorage, storageKey, retained);
      setIntent(saved);
      // First submit stays in the native API form; recovery reuses its exact
      // persisted body through the shared scope/generation boundary.
      await recovery.retry();
    } catch {
      setNotice(t`Receipt outcome unconfirmed. Retry the original receipt.`);
    }
  }
  const readback = receipt ?? pending;
  const content = (
    <Stack gap='xs' style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
      {query.isFetching && <Text size='sm'>{t`Reading receipt status…`}</Text>}
      {query.isError && (
        <Alert color='red'>
          {t`Receipt status unavailable`}{' '}
          <Button
            variant='subtle'
            size='compact-sm'
            onClick={() => void query.refetch()}
          >{t`Retry`}</Button>
        </Alert>
      )}
      {context?.binding && (
        <Anchor
          component={Link}
          to={`/maintenance/work-orders/${context.binding.work_order}?work_panel=parts`}
        >
          {context.binding.reference}
        </Anchor>
      )}
      {notice && <Alert color='orange'>{notice}</Alert>}
      {recovery.blocked && <Alert color='red'>{recovery.notice}</Alert>}
      {readback && (
        <Stack gap={4}>
          <Text fw={600} size='sm'>
            {readback.reservation_state === 'allocated'
              ? t`Receipt recorded · reservation recorded`
              : readback.reservation_state === 'attention'
                ? t`Receipt recorded · reservation needs attention`
                : t`Receipt recorded · reservation pending`}
          </Text>
          <Group gap='xs'>
            {readback.stocks.map((stock) => (
              <Anchor
                key={stock.stock_item}
                component={Link}
                to={`/stock/item/${stock.stock_item}/`}
                size='sm'
              >
                {t`Stock ${stock.stock_item}`} · {stock.quantity} ·{' '}
                {stock.batch}
              </Anchor>
            ))}
          </Group>
          {!!readback.reservation_code && (
            <Text size='sm'>{readback.reservation_code}</Text>
          )}
        </Stack>
      )}
      {readback &&
        readback.actor !== actor &&
        readback.reservation_state !== 'allocated' && (
          <Text size='sm'>{t`Original receiving actor required`}</Text>
        )}
      {retained && !readback && (
        <Alert color='orange'>{t`Receipt outcome unconfirmed`}</Alert>
      )}
      {retained && readback?.reservation_state !== 'allocated' && (
        <Button
          variant='default'
          loading={busy}
          disabled={
            recovery.blocked ||
            query.isError ||
            (readback && readback.actor !== actor)
          }
          onClick={() => void recover()}
        >{t`Retry original receipt`}</Button>
      )}
    </Stack>
  );
  return {
    formIdentity: `${storageKey}:${generation}`,
    context,
    content,
    disabled,
    intent: retained,
    prepare,
    dismiss: () => {
      if (!current()) return;
      if (receipt?.reservation_state === 'allocated') {
        if (retained) recovery.clearConfirmed(retained);
        if (
          intent &&
          localStorage.getItem(storageKey) === JSON.stringify(intent)
        )
          localStorage.removeItem(storageKey);
        setIntent(null);
      }
    },
    onSuccess: () => {
      void refresh();
    },
    onError: (error: any) => {
      if (!current()) return;
      // A first HTTP 400 proves this new transaction rolled back. Uncertain
      // failures and committed replays retain the exact body for recovery.
      if (
        error?.status === 400 &&
        submitted.current?.storage === storageKey &&
        submitted.current.generation === generation &&
        !receipt
      ) {
        if (
          localStorage.getItem(storageKey) ===
          JSON.stringify(submitted.current.request)
        )
          localStorage.removeItem(storageKey);
        submitted.current = null;
        setIntent(null);
      }
      setNotice(t`Receipt not confirmed. Review the result before retrying.`);
      void query.refetch();
    }
  };
}
