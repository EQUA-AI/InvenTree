import type {
  MaintenanceProcurementContext,
  ProcurementLineContext,
  ProcurementOffer,
  ProcurementProvenance
} from '@lib/types/Procurement';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Checkbox,
  Divider,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../../App';
import { CommandNotice } from './CommandNotice';
import { EvidenceFields, blankEvidence } from './ProcurementEvidence';
import { useProcurementCommand } from './useProcurementCommand';

type Offer = ProcurementOffer;
const emptyOffer = (): Offer => ({
  key: crypto.randomUUID(),
  supplier_part_id: 0,
  quote_reference: '',
  revision: '',
  order_quantity: '',
  order_uom: '',
  base_units_per_order_unit: '',
  unit_price: null,
  discount: null,
  freight: null,
  tax: null,
  other_charges: null,
  delivered_total: null,
  currency: '',
  terms: null,
  promised_at: null,
  promise_basis: 'unknown',
  valid_through: null,
  technical_eligibility: 'unresolved',
  source: blankEvidence()
});

export function ProvenanceFields({
  value,
  onChange
}: Readonly<{
  value: ProcurementProvenance;
  onChange: (value: ProcurementProvenance) => void;
}>) {
  return (
    <Stack gap='xs'>
      <Checkbox
        label={t`Representative fixture`}
        checked={value.mode === 'recorded_fixture'}
        onChange={(event) =>
          onChange({
            ...value,
            mode: event.currentTarget.checked
              ? 'recorded_fixture'
              : 'live_test',
            fixture_namespace: event.currentTarget.checked ? '' : null
          })
        }
      />
      {value.mode === 'recorded_fixture' && (
        <TextInput
          label={t`Fixture namespace`}
          required
          value={value.fixture_namespace ?? ''}
          onChange={(event) =>
            onChange({ ...value, fixture_namespace: event.currentTarget.value })
          }
        />
      )}
      <TextInput
        label={t`Evidence reference`}
        required
        value={value.reference}
        onChange={(event) =>
          onChange({ ...value, reference: event.currentTarget.value })
        }
      />
    </Stack>
  );
}
export const emptyProvenance = (): ProcurementProvenance => ({
  mode: 'live_test',
  method: 'human_review',
  reference: '',
  fixture_namespace: null
});

/** A source record is explicit evidence, independent of PO approval. */
export function SourceRecordForm({
  context,
  line,
  onSaved
}: Readonly<{
  context: MaintenanceProcurementContext;
  line: ProcurementLineContext;
  onSaved: () => void;
}>) {
  const [offers, setOffers] = useState<Offer[]>([emptyOffer()]);
  const [selected, setSelected] = useState<string | null>(null);
  const [technical, setTechnical] = useState(blankEvidence());
  const [deadline, setDeadline] = useState(blankEvidence());
  const [needed, setNeeded] = useState('');
  const [step, setStep] = useState('');
  const [sourceAt, setSourceAt] = useState('');
  const [reason, setReason] = useState('');
  const [provenance, setProvenance] = useState(emptyProvenance());
  const command = useProcurementCommand(
    `source:${context.work_order_id}:${line.kit_line_id}`,
    onSaved
  );
  const parts = useQuery({
    queryKey: ['procurement-supplier-parts', line.part_id],
    retry: false,
    enabled: !!line.part_id,
    queryFn: async ({ signal }) => {
      const data = (
        await api.get('/api/company/part/', {
          params: {
            part: line.part_id,
            active: true,
            supplier_detail: true,
            limit: 500
          },
          signal
        })
      ).data;
      return (Array.isArray(data) ? data : data.results) as {
        pk: number;
        SKU: string;
        supplier: number;
        supplier_detail?: { name: string };
      }[];
    }
  });
  function change(index: number, next: Partial<Offer>) {
    setOffers((current) =>
      current.map((offer, i) => (i === index ? { ...offer, ...next } : offer))
    );
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (
          !selected ||
          !technical.sha256 ||
          offers.some((offer) => !offer.source.sha256) ||
          (needed && !deadline.sha256)
        )
          return;
        void command.submit(
          `/api/tasks/work-orders/${context.work_order_id}/procurement/source-selection/`,
          'source',
          {
            expected_version: context.work_lifecycle_version,
            kit_line_id: line.kit_line_id,
            requirement_version: line.requirement_version,
            configuration_revision: line.configuration_revision,
            source_at: sourceAt,
            provenance,
            needed_at: needed || null,
            deadline_source: needed ? deadline : null,
            dependent_step: step || null,
            technical_evidence: [technical],
            offers,
            selected_offer_key: selected,
            reason,
            supersedes_event_id: line.source_selection?.event_id ?? null
          }
        );
      }}
    >
      <Stack gap='md'>
        <CommandNotice command={command} />
        <TextInput
          label={t`Source time (ISO 8601 with timezone)`}
          required
          placeholder='2026-10-23T09:00:00+03:00'
          value={sourceAt}
          onChange={(event) => setSourceAt(event.currentTarget.value)}
        />
        <ProvenanceFields value={provenance} onChange={setProvenance} />
        <EvidenceFields
          workId={context.work_order_id}
          label={t`Technical source PDF`}
          value={technical}
          onChange={setTechnical}
        />
        <TextInput
          label={t`Needed by (ISO 8601 with timezone)`}
          value={needed}
          onChange={(event) => setNeeded(event.currentTarget.value)}
        />
        {needed && (
          <EvidenceFields
            workId={context.work_order_id}
            label={t`Deadline source PDF`}
            value={deadline}
            onChange={setDeadline}
          />
        )}
        <TextInput
          label={t`Dependent step`}
          value={step}
          onChange={(event) => setStep(event.currentTarget.value)}
        />
        {parts.isError && (
          <Alert color='red'>{t`Supplier parts could not be read`}</Alert>
        )}
        {offers.map((offer, index) => (
          <Stack key={offer.key} gap='sm'>
            <Divider label={t`Offer ${index + 1}`} />
            <Select
              label={t`Supplier part`}
              searchable
              required
              value={
                offer.supplier_part_id ? String(offer.supplier_part_id) : null
              }
              data={(parts.data ?? []).map((part) => ({
                value: String(part.pk),
                label: `${part.supplier_detail?.name ?? part.supplier} · ${part.SKU}`
              }))}
              onChange={(value) =>
                change(index, { supplier_part_id: Number(value) })
              }
            />
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing='sm'>
              <TextInput
                label={t`Quote reference`}
                required
                value={offer.quote_reference}
                onChange={(event) =>
                  change(index, { quote_reference: event.currentTarget.value })
                }
              />
              <TextInput
                label={t`Quote revision`}
                required
                value={offer.revision}
                onChange={(event) =>
                  change(index, { revision: event.currentTarget.value })
                }
              />
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing='sm'>
              <TextInput
                label={t`Order quantity`}
                required
                value={offer.order_quantity}
                onChange={(event) =>
                  change(index, { order_quantity: event.currentTarget.value })
                }
              />
              <TextInput
                label={t`Order unit`}
                required
                value={offer.order_uom}
                onChange={(event) =>
                  change(index, { order_uom: event.currentTarget.value })
                }
              />
              <TextInput
                label={t`Base units per order unit`}
                required
                value={offer.base_units_per_order_unit}
                onChange={(event) =>
                  change(index, {
                    base_units_per_order_unit: event.currentTarget.value
                  })
                }
              />
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing='sm'>
              <TextInput
                label={t`Currency`}
                required
                maxLength={3}
                value={offer.currency}
                onChange={(event) =>
                  change(index, { currency: event.currentTarget.value })
                }
              />
              <TextInput
                label={t`Unit price`}
                value={offer.unit_price ?? ''}
                onChange={(event) =>
                  change(index, {
                    unit_price: event.currentTarget.value || null
                  })
                }
              />
              <TextInput
                label={t`Discount amount`}
                value={offer.discount ?? ''}
                onChange={(event) =>
                  change(index, { discount: event.currentTarget.value || null })
                }
              />
            </SimpleGrid>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing='sm'>
              <TextInput
                label={t`Freight`}
                value={offer.freight ?? ''}
                onChange={(event) =>
                  change(index, { freight: event.currentTarget.value || null })
                }
              />
              <TextInput
                label={t`Tax`}
                value={offer.tax ?? ''}
                onChange={(event) =>
                  change(index, { tax: event.currentTarget.value || null })
                }
              />
              <TextInput
                label={t`Other charges`}
                value={offer.other_charges ?? ''}
                onChange={(event) =>
                  change(index, {
                    other_charges: event.currentTarget.value || null
                  })
                }
              />
              <TextInput
                label={t`Delivered total`}
                value={offer.delivered_total ?? ''}
                onChange={(event) =>
                  change(index, {
                    delivered_total: event.currentTarget.value || null
                  })
                }
              />
            </SimpleGrid>
            <TextInput
              label={t`Terms`}
              value={offer.terms ?? ''}
              onChange={(event) =>
                change(index, { terms: event.currentTarget.value || null })
              }
            />
            <TextInput
              label={t`Promised arrival (ISO 8601 with timezone)`}
              value={offer.promised_at ?? ''}
              onChange={(event) =>
                change(index, {
                  promised_at: event.currentTarget.value || null
                })
              }
            />
            <Select
              label={t`Arrival basis`}
              value={offer.promise_basis}
              data={[
                { value: 'unknown', label: t`Unknown` },
                { value: 'estimate', label: t`Estimate` },
                { value: 'supplier_quote', label: t`Supplier quote` }
              ]}
              onChange={(value) =>
                change(index, {
                  promise_basis: value as Offer['promise_basis']
                })
              }
            />
            <TextInput
              label={t`Valid through (ISO 8601 with timezone)`}
              value={offer.valid_through ?? ''}
              onChange={(event) =>
                change(index, {
                  valid_through: event.currentTarget.value || null
                })
              }
            />
            <Select
              label={t`Technical eligibility`}
              value={offer.technical_eligibility}
              data={[
                { value: 'unresolved', label: t`Unresolved` },
                { value: 'supported', label: t`Supported by source` },
                { value: 'not_eligible', label: t`Not eligible` }
              ]}
              onChange={(value) =>
                change(index, {
                  technical_eligibility: value as Offer['technical_eligibility']
                })
              }
            />
            <EvidenceFields
              workId={context.work_order_id}
              label={t`Quote PDF`}
              value={offer.source}
              onChange={(value) => change(index, { source: value })}
            />
            {offers.length > 1 && (
              <Button
                variant='subtle'
                color='red'
                onClick={() => {
                  setOffers((current) => current.filter((_, i) => i !== index));
                  if (selected === offer.key) setSelected(null);
                }}
              >{t`Remove offer`}</Button>
            )}
          </Stack>
        ))}
        <Button
          variant='light'
          onClick={() => setOffers((current) => [...current, emptyOffer()])}
        >{t`Add offer`}</Button>
        <Select
          label={t`Selected offer`}
          required
          value={selected}
          onChange={setSelected}
          data={offers.map((offer, index) => ({
            value: offer.key,
            label: offer.quote_reference || t`Offer ${index + 1}`
          }))}
        />
        <Textarea
          label={t`Selection reason`}
          required
          value={reason}
          onChange={(event) => setReason(event.currentTarget.value)}
        />
        <Button
          type='submit'
          loading={command.pending}
          disabled={
            !!command.retained ||
            !selected ||
            !technical.sha256 ||
            offers.some(
              (offer) => !offer.source.sha256 || !offer.supplier_part_id
            ) ||
            (!!needed && !deadline.sha256)
          }
        >{t`Record source selection`}</Button>
        <Text
          size='xs'
          c='dimmed'
        >{t`Evidence only · Purchase requires named approval`}</Text>
      </Stack>
    </form>
  );
}
