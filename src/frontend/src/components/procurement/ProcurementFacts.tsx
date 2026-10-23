import type {
  ProcurementLineContext,
  ProcurementOrderContext
} from '@lib/types/Procurement';
import { t } from '@lingui/core/macro';
import { Badge, Stack, Text } from '@mantine/core';
import dayjs from 'dayjs';
import { StatusBadge } from '../../pages/assets/health/StatusBadge';
import { MissingValue, ValueTooltip } from '../items/ValueTooltip';
import { EvidenceLink } from './ProcurementEvidence';
import { DocumentDownload } from './RecordedDocument';
import { selectedOffer } from './procurement';

export const procurementTime = (value: string) =>
  dayjs(value).format('DD MMM YYYY, HH:mm Z');
export function SourceFacts({
  line,
  workId
}: Readonly<{ line: ProcurementLineContext; workId: number }>) {
  const source = line.source_selection;
  const offer = selectedOffer(line);
  if (!source || !offer)
    return <MissingValue reason={t`No source selection recorded`} />;
  return (
    <Stack gap={4}>
      {line.selection_current === false && (
        <StatusBadge
          tone='warning'
          color='orange'
          size='sm'
          multiline
        >{t`Source needs review`}</StatusBadge>
      )}
      {source.snapshot.provenance.mode === 'recorded_fixture' && (
        <Badge variant='outline' color='gray' size='sm'>{t`Fixture`}</Badge>
      )}
      <Text size='sm' fw={600}>
        {offer.quote_reference}
      </Text>
      <Text size='sm'>
        {offer.delivered_total === null ? (
          <MissingValue reason={t`Complete delivered cost is not known`} />
        ) : (
          `${offer.currency} ${offer.delivered_total}`
        )}
      </Text>
      <Text size='sm'>{source.snapshot.reason}</Text>
      {offer.promise_basis !== 'supplier_quote' && offer.promised_at && (
        <Text size='sm'>
          {offer.promise_basis === 'estimate'
            ? t`Estimated arrival`
            : t`Arrival basis unknown`}
        </Text>
      )}
      {offer.technical_eligibility !== 'supported' && (
        <Text
          size='sm'
          c='light-dark(var(--mantine-color-orange-9), var(--mantine-color-orange-3))'
        >
          {offer.technical_eligibility === 'not_eligible'
            ? t`Not eligible`
            : t`Fit unresolved`}
        </Text>
      )}
      <Text size='sm'>
        {offer.promised_at ? (
          <ValueTooltip
            label={
              offer.promise_basis === 'supplier_quote'
                ? t`Arrival from supplier quote`
                : offer.promise_basis === 'estimate'
                  ? t`Estimated arrival`
                  : t`Arrival basis not known`
            }
          >
            {procurementTime(offer.promised_at)}
          </ValueTooltip>
        ) : (
          <MissingValue reason={t`Arrival is not recorded`} />
        )}
      </Text>
      <details>
        <summary
          style={{
            cursor: 'pointer',
            color:
              'light-dark(var(--mantine-primary-color-8), var(--mantine-primary-color-3))',
            fontSize: 'var(--mantine-font-size-sm)'
          }}
        >{t`Source evidence`}</summary>
        <Stack gap='xs' mt='xs'>
          {source.snapshot.technical_evidence.map((evidence, index) => (
            <EvidenceLink
              key={`${evidence.attachment_id}:${index}`}
              workId={workId}
              evidence={evidence}
              label={t`Technical source`}
            />
          ))}
          {source.snapshot.deadline_source && (
            <EvidenceLink
              workId={workId}
              evidence={source.snapshot.deadline_source}
              label={t`Deadline source`}
            />
          )}
          {source.snapshot.offers.map((entry) => (
            <Stack key={entry.key} gap={2}>
              <EvidenceLink
                workId={workId}
                evidence={entry.source}
                label={entry.quote_reference}
              />
              <Text size='sm'>
                {entry.delivered_total === null ? (
                  <MissingValue reason={t`Delivered cost is not known`} />
                ) : (
                  `${entry.currency} ${entry.delivered_total}`
                )}
              </Text>
              <Text size='sm'>
                {entry.promised_at ? (
                  procurementTime(entry.promised_at)
                ) : (
                  <MissingValue reason={t`Arrival is not recorded`} />
                )}
              </Text>
              <Text size='sm'>
                {entry.technical_eligibility === 'supported'
                  ? t`Supported by source`
                  : entry.technical_eligibility === 'not_eligible'
                    ? t`Not eligible`
                    : t`Fit unresolved`}
              </Text>
            </Stack>
          ))}
          <Text size='xs'>
            {procurementTime(source.source_at ?? source.recorded_at)}
            {source.actor.label ? ` · ${source.actor.label}` : ''}
          </Text>
        </Stack>
      </details>
    </Stack>
  );
}
export function PlacedFact({
  order
}: Readonly<{ order: ProcurementOrderContext }>) {
  const execution = order.binding?.snapshot.approval_execution;
  return (
    <Stack gap={2}>
      {order.placed ? (
        <Text size='sm'>{dayjs(order.placed.date).format('DD MMM YYYY')}</Text>
      ) : (
        <MissingValue reason={t`Placement date is not recorded`} />
      )}
      {execution ? (
        <ValueTooltip
          label={t`Named approval ${execution.approval_id} · revision ${execution.revision}`}
        >
          <Text span size='sm'>
            {execution.actor_label}
          </Text>
        </ValueTooltip>
      ) : (
        <MissingValue reason={t`Named approval execution is not linked`} />
      )}
    </Stack>
  );
}
export function DocumentFact({
  order
}: Readonly<{ order: ProcurementOrderContext }>) {
  return order.recorded_document ? (
    <Stack gap={4}>
      <Text size='sm'>{t`Recorded · not sent`}</Text>
      <Text size='xs'>
        {procurementTime(order.recorded_document.recorded_at)}
      </Text>
      <DocumentDownload document={order.recorded_document} />
    </Stack>
  ) : (
    <MissingValue reason={t`No recorded PDF or email available to you`} />
  );
}
export function AcknowledgmentFact({
  order,
  workId
}: Readonly<{ order: ProcurementOrderContext; workId: number }>) {
  const ack = order.acknowledgment;
  if (!ack)
    return <MissingValue reason={t`Supplier acknowledgment is not recorded`} />;
  return (
    <Stack gap={2}>
      {order.acknowledgment_current === false && (
        <StatusBadge
          tone='warning'
          color='orange'
          size='sm'
          multiline
        >{t`Acknowledgment needs review`}</StatusBadge>
      )}
      <Text size='sm'>
        {ack.snapshot.supplier_reference} · {ack.snapshot.accepted_quantity}
        {ack.snapshot.order_uom ? ` ${ack.snapshot.order_uom}` : ''}
      </Text>
      <Text size='sm'>{procurementTime(ack.snapshot.promised_at)}</Text>
      <EvidenceLink
        workId={workId}
        orderId={order.purchase_order_id}
        evidence={ack.snapshot.source}
        label={t`Acknowledgment`}
      />
      <ValueTooltip
        label={t`Source time ${procurementTime(ack.source_at ?? ack.recorded_at)} · recorded ${procurementTime(ack.recorded_at)}`}
      >
        <Text span size='xs'>
          {ack.actor.label ?? (
            <MissingValue reason={t`Recorder is not available`} />
          )}
        </Text>
      </ValueTooltip>
      {ack.snapshot.deviations.map((deviation, index) => (
        <Text
          key={index}
          size='sm'
          c='light-dark(var(--mantine-color-orange-9), var(--mantine-color-orange-3))'
        >
          {deviation}
        </Text>
      ))}
    </Stack>
  );
}
