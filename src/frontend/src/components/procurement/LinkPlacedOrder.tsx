import { ModelType } from '@lib/enums/ModelType';
import { t } from '@lingui/core/macro';
import { Button, Select, Stack } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../App';
import useStatusCodes from '../../hooks/UseStatusCodes';
import { CommandNotice } from './CommandNotice';
import { useProcurementCommand } from './useProcurementCommand';

/** Explicit exact native line choice; the existing link endpoint revalidates
 * placed state, SupplierPart, quantity, machine scope and purchasing authority.
 */
export function LinkPlacedOrder({
  workId,
  partId,
  shortageId,
  onSaved
}: Readonly<{
  workId: number;
  partId: number;
  shortageId: number;
  onSaved: () => void;
}>) {
  const statusCodes = useStatusCodes({ modelType: ModelType.purchaseorder });
  const [selected, setSelected] = useState<string | null>(null);
  const lines = useQuery({
    queryKey: ['procurement-native-lines', partId],
    retry: false,
    queryFn: async ({ signal }) => {
      const data = (
        await api.get('/api/order/po-line/', {
          params: { base_part: partId, order_detail: true, limit: 500 },
          signal
        })
      ).data;
      return (Array.isArray(data) ? data : data.results) as {
        pk: number;
        quantity: string;
        received: string;
        order_detail: { reference: string; status: number };
      }[];
    }
  });
  const command = useProcurementCommand(
    `link:${workId}:${shortageId}`,
    onSaved
  );
  return (
    <Stack gap='sm'>
      <CommandNotice command={command} />
      <Select
        label={t`Placed PO line`}
        searchable
        required
        value={selected}
        onChange={setSelected}
        error={
          lines.isError ? t`Purchase order lines could not be read` : undefined
        }
        data={(lines.data ?? [])
          .filter((line) => line.order_detail?.status === statusCodes.PLACED)
          .map((line) => ({
            value: String(line.pk),
            label: `${line.order_detail.reference} · ${line.pk} · ${line.quantity}`
          }))}
      />
      <Button
        disabled={!selected || !!command.retained}
        loading={command.pending}
        onClick={() =>
          void command.submit(
            `/api/tasks/work-orders/${workId}/job-kit/shortages/${shortageId}/link-po/`,
            'link',
            { purchase_order_line_id: Number(selected) }
          )
        }
      >{t`Link placed order`}</Button>
    </Stack>
  );
}
