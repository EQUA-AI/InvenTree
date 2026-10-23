import { t } from '@lingui/core/macro';
import { Alert, Button, Group, Paper, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import {
  useAllocationIssueRecovery,
  useAllocationUseRecovery
} from '../../../components/records/useProcurementRecovery';
import type { MaterialReservation } from './materials';

/** Explicit physical custody; a use confirmation never establishes fitted identity. */
export function AllocationCustody({
  workOrderId,
  reservation,
  writable,
  stale,
  consumable
}: {
  workOrderId: number;
  reservation: MaterialReservation;
  writable: boolean;
  stale: boolean;
  consumable: boolean;
}) {
  const [confirm, setConfirm] = useState<'issue' | 'use' | null>(null);
  const issued = useAllocationIssueRecovery({
    workOrderId,
    allocationId: reservation.id,
    onSaved: () => setConfirm(null)
  });
  const used = useAllocationUseRecovery({
    workOrderId,
    allocationId: reservation.id,
    onSaved: () => setConfirm(null)
  });
  const retained = issued.retained || used.retained;
  const blocked = issued.blocked || used.blocked;
  const pending = issued.pending || used.pending;
  if (!writable && !retained && !blocked) return null;
  return (
    <Stack gap='xs'>
      {[
        { operation: issued, label: t`Recover original issue` },
        { operation: used, label: t`Recover original material use` }
      ].map(
        ({ operation, label }) =>
          operation.notice && (
            <Alert key={label} color={operation.retained ? 'orange' : 'blue'}>
              <Stack gap='xs'>
                {operation.notice}
                {operation.retained && (
                  <Button
                    disabled={operation.blocked || stale}
                    loading={operation.pending}
                    onClick={() => void operation.retry()}
                  >
                    {label}
                  </Button>
                )}
              </Stack>
            </Alert>
          )
      )}
      {writable && !retained && !blocked && (
        <Group gap='xs'>
          {['reserved', 'staged'].includes(reservation.status) && (
            <Button
              size='xs'
              variant='default'
              disabled={stale || pending}
              onClick={() => setConfirm('issue')}
            >{t`Record issue`}</Button>
          )}
          {consumable && reservation.status === 'issued' && (
            <Button
              size='xs'
              variant='default'
              disabled={stale || pending}
              onClick={() => setConfirm('use')}
            >{t`Record material use`}</Button>
          )}
        </Group>
      )}
      {confirm && writable && !retained && (
        <Paper withBorder p='sm'>
          <Stack gap='xs'>
            <Text size='sm'>
              {confirm === 'use'
                ? t`Remove the allocated quantity from this exact stock item and record its use?`
                : t`Record this allocated stock leaving stores with you as the issuing person?`}
            </Text>
            <Text size='sm'>
              {t`Stock item`} {reservation.stock_item?.pk} ·{' '}
              {reservation.stock_item?.batch} · {reservation.quantity}
            </Text>
            <Text
              size='xs'
              c='dimmed'
            >{t`Material use does not establish fit or permission to operate.`}</Text>
            <Group>
              <Button
                disabled={stale || pending || blocked}
                onClick={() =>
                  void (confirm === 'use' ? used.submit({}) : issued.submit({}))
                }
              >
                {confirm === 'use' ? t`Confirm material use` : t`Confirm issue`}
              </Button>
              <Button
                variant='subtle'
                onClick={() => setConfirm(null)}
              >{t`Cancel`}</Button>
            </Group>
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}
