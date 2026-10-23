import type {
  MaintenanceProcurementContext,
  ProcurementLineContext
} from '@lib/types/Procurement';
import { t } from '@lingui/core/macro';
import { Button, Select, Stack, TextInput, Textarea } from '@mantine/core';
import { useState } from 'react';
import { CommandNotice } from './CommandNotice';
import { EvidenceFields, blankEvidence } from './ProcurementEvidence';
import { ProvenanceFields, emptyProvenance } from './SourceRecordForm';
import { useProcurementCommand } from './useProcurementCommand';

export function AcknowledgmentForm({
  context,
  line,
  onSaved
}: Readonly<{
  context: MaintenanceProcurementContext;
  line: ProcurementLineContext;
  onSaved: () => void;
}>) {
  const [orderLine, setOrderLine] = useState<string | null>(
    line.orders.length === 1
      ? String(line.orders[0].purchase_order_line_id)
      : null
  );
  const [sourceAt, setSourceAt] = useState('');
  const [reference, setReference] = useState('');
  const [quantity, setQuantity] = useState('');
  const [terms, setTerms] = useState('');
  const [promised, setPromised] = useState('');
  const [deviations, setDeviations] = useState('');
  const [source, setSource] = useState(blankEvidence());
  const [provenance, setProvenance] = useState(emptyProvenance());
  const order = line.orders.find(
    (entry) => String(entry.purchase_order_line_id) === orderLine
  );
  const command = useProcurementCommand(
    `ack:${context.work_order_id}:${line.kit_line_id}`,
    onSaved
  );
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (!order || !source.sha256) return;
        void command.submit(
          `/api/tasks/work-orders/${context.work_order_id}/procurement/acknowledgment/`,
          'ack',
          {
            expected_version: context.work_lifecycle_version,
            kit_line_id: line.kit_line_id,
            source_at: sourceAt,
            provenance,
            purchase_order_line_id: order.purchase_order_line_id,
            supplier_reference: reference,
            accepted_quantity: quantity,
            terms,
            promised_at: promised,
            source,
            deviations: deviations
              .split('\n')
              .map((item) => item.trim())
              .filter(Boolean)
          }
        );
      }}
    >
      <Stack gap='sm'>
        <CommandNotice command={command} />
        <Select
          label={t`PO line`}
          required
          value={orderLine}
          onChange={setOrderLine}
          data={line.orders.map((entry) => ({
            value: String(entry.purchase_order_line_id),
            label: `${entry.reference} · ${entry.purchase_order_line_id}`
          }))}
        />
        <TextInput
          label={t`Supplier reference`}
          required
          value={reference}
          onChange={(event) => setReference(event.currentTarget.value)}
        />
        <TextInput
          label={t`Accepted quantity (order units)`}
          required
          value={quantity}
          onChange={(event) => setQuantity(event.currentTarget.value)}
        />
        <TextInput
          label={t`Terms`}
          required
          value={terms}
          onChange={(event) => setTerms(event.currentTarget.value)}
        />
        <TextInput
          label={t`Source time (ISO 8601 with timezone)`}
          required
          value={sourceAt}
          onChange={(event) => setSourceAt(event.currentTarget.value)}
        />
        <TextInput
          label={t`Promised arrival (ISO 8601 with timezone)`}
          required
          value={promised}
          onChange={(event) => setPromised(event.currentTarget.value)}
        />
        <EvidenceFields
          key={order?.purchase_order_id}
          workId={context.work_order_id}
          orderId={order?.purchase_order_id}
          label={t`Acknowledgment PDF`}
          value={source}
          onChange={setSource}
        />
        <Textarea
          label={t`Deviations (one per line)`}
          value={deviations}
          onChange={(event) => setDeviations(event.currentTarget.value)}
        />
        <ProvenanceFields value={provenance} onChange={setProvenance} />
        <Button
          type='submit'
          loading={command.pending}
          disabled={!!command.retained || !order || !source.sha256}
        >{t`Record acknowledgment`}</Button>
      </Stack>
    </form>
  );
}
