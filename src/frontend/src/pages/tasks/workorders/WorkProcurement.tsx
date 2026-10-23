import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { getDetailUrl } from '@lib/functions/Navigation';
import type {
  MaintenanceProcurementContext,
  ProcurementLineContext
} from '@lib/types/Procurement';
import { t } from '@lingui/core/macro';
import {
  Anchor,
  Button,
  Modal,
  Paper,
  SimpleGrid,
  Stack,
  Text
} from '@mantine/core';
import { type ReactNode, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  MissingValue,
  ValueTooltip
} from '../../../components/items/ValueTooltip';
import { AcknowledgmentForm } from '../../../components/procurement/AcknowledgmentForm';
import { LinkPlacedOrder } from '../../../components/procurement/LinkPlacedOrder';
import { NamedApproval } from '../../../components/procurement/NamedApproval';
import procurementClasses from '../../../components/procurement/Procurement.module.css';
import {
  AcknowledgmentFact,
  DocumentFact,
  PlacedFact,
  SourceFacts,
  procurementTime
} from '../../../components/procurement/ProcurementFacts';
import { SourceRecordForm } from '../../../components/procurement/SourceRecordForm';
import { placementPayload } from '../../../components/procurement/procurement';
import useStatusCodes from '../../../hooks/UseStatusCodes';
import { useUserState } from '../../../states/UserState';
import { StatusBadge } from '../../assets/health/StatusBadge';
import classes from './WorkProcurement.module.css';
import type { MaterialReadout, MaterialRequirement } from './materials';

const orderUrl = (pk: number, workId: number) =>
  `${getDetailUrl(ModelType.purchaseorder, pk)}?workOrder=${workId}`;

function Cell({
  label,
  children
}: Readonly<{ label: string; children: ReactNode }>) {
  return (
    <Stack gap={4} className={classes.cell}>
      <Text size='xs' fw={600} hiddenFrom='lg' c='dimmed'>
        {label}
      </Text>
      {children}
    </Stack>
  );
}
function ProcurementRow({
  context,
  line,
  requirement,
  stale,
  details
}: Readonly<{
  context: MaintenanceProcurementContext;
  line: ProcurementLineContext;
  requirement?: MaterialRequirement;
  stale: boolean;
  details?: ReactNode;
}>) {
  const user = useUserState();
  const statusCodes = useStatusCodes({ modelType: ModelType.purchaseorder });
  const [action, setAction] = useState<
    'source' | 'approval' | 'ack' | 'link' | null
  >(null);
  const canEdit =
    user.hasChangeRole(UserRoles.purchase_order) &&
    (user.isSuperuser() || user.user?.permissions?.jobkit?.includes('manage'));
  const payload = placementPayload(line);
  const shortage = requirement?.shortages?.find((entry) =>
    ['open', 'requested'].includes(entry.status)
  );
  const canBuy =
    canEdit &&
    !!payload &&
    !!shortage &&
    line.orders.length === 0 &&
    Number(line.outstanding_quantity) > 0;
  const labels = [
    t`Requirement`,
    t`Source / offer`,
    t`Purchase order`,
    t`Document / acknowledgment`,
    t`Receipt`,
    t`Reservation`
  ];
  return (
    <Paper
      withBorder
      radius='sm'
      p='sm'
      data-procurement-line={line.kit_line_id}
    >
      <SimpleGrid
        cols={{ base: 1, sm: 2, lg: 6 }}
        spacing='md'
        verticalSpacing='md'
      >
        <Cell label={labels[0]}>
          {requirement?.part && user.hasViewRole(UserRoles.part) ? (
            <Anchor
              component={Link}
              to={getDetailUrl(ModelType.part, requirement.part.pk)}
              fw={600}
              size='sm'
            >
              {requirement.part.name}
            </Anchor>
          ) : (
            <Text size='sm' fw={600}>
              {requirement?.part?.name ?? (
                <MissingValue reason={t`Part identity is not available`} />
              )}
            </Text>
          )}
          {requirement?.part?.ipn && (
            <Text size='xs'>{requirement.part.ipn}</Text>
          )}
          <Text size='sm'>
            {line.required_quantity}
            {line.base_uom ? ` ${line.base_uom}` : ''}
          </Text>
          <Text size='sm'>
            {line.needed_at ? (
              <ValueTooltip
                label={
                  line.dependent_step
                    ? t`Needed for ${line.dependent_step}`
                    : t`Requirement deadline from source`
                }
              >
                {procurementTime(line.needed_at)}
              </ValueTooltip>
            ) : line.work_due_date ? (
              <ValueTooltip
                label={t`Work due date only; a precise material deadline is not recorded`}
              >
                {line.work_due_date}
              </ValueTooltip>
            ) : (
              <MissingValue reason={t`Requirement deadline is not recorded`} />
            )}
          </Text>
          {line.dependent_step && <Text size='sm'>{line.dependent_step}</Text>}
          {canEdit && (
            <Button
              size='compact-xs'
              variant='subtle'
              disabled={stale || context.pending_operations.length > 0}
              onClick={() => setAction('source')}
            >
              {line.source_selection ? t`Revise source` : t`Record source`}
            </Button>
          )}
        </Cell>
        <Cell label={labels[1]}>
          <SourceFacts line={line} workId={context.work_order_id} />
          {canBuy && (
            <Button
              size='xs'
              variant='light'
              disabled={stale || context.pending_operations.length > 0}
              onClick={() => setAction('approval')}
            >{t`Request named approval`}</Button>
          )}
          {canEdit &&
            line.selection_current &&
            !payload &&
            line.orders.length === 0 && (
              <ValueTooltip
                label={t`Review fit, quote validity, complete costs, sourced deadline and arrival. Nonzero discounts require native purchasing preparation.`}
              >
                <Text size='sm'>{t`Source needs review`}</Text>
              </ValueTooltip>
            )}
        </Cell>
        <Cell label={labels[2]}>
          {line.orders.length ? (
            line.orders.map((order) => (
              <Stack key={order.purchase_order_line_id} gap={4}>
                <Anchor
                  component={Link}
                  to={orderUrl(order.purchase_order_id, context.work_order_id)}
                  size='sm'
                  fw={600}
                >
                  {order.reference}
                </Anchor>
                <Text size='sm'>{order.status_label}</Text>
                <Text size='sm'>
                  {order.order_total === null ? (
                    <MissingValue
                      reason={t`Complete order total is not known`}
                    />
                  ) : (
                    `${order.currency} ${order.order_total}`
                  )}
                </Text>
                <PlacedFact order={order} />
              </Stack>
            ))
          ) : (
            <MissingValue reason={t`No native purchase order linked`} />
          )}
          {canEdit && shortage && line.part_id && (
            <Button
              size='compact-xs'
              variant='subtle'
              disabled={stale}
              onClick={() => setAction('link')}
            >{t`Link placed order`}</Button>
          )}
        </Cell>
        <Cell label={labels[3]}>
          {line.orders.length ? (
            line.orders.map((order) => (
              <Stack key={order.purchase_order_line_id} gap='xs'>
                {line.orders.length > 1 && (
                  <Text size='sm' fw={600}>
                    {order.reference}
                  </Text>
                )}
                <DocumentFact order={order} />
                {canEdit && !order.recorded_document && (
                  <Anchor
                    component={Link}
                    to={orderUrl(
                      order.purchase_order_id,
                      context.work_order_id
                    )}
                    size='sm'
                  >{t`Record document`}</Anchor>
                )}
                <AcknowledgmentFact
                  order={order}
                  workId={context.work_order_id}
                />
              </Stack>
            ))
          ) : (
            <MissingValue
              reason={t`No linked order document or acknowledgment`}
            />
          )}
          {canEdit && line.orders.length > 0 && (
            <Button
              size='compact-xs'
              variant='subtle'
              disabled={stale || context.pending_operations.length > 0}
              onClick={() => setAction('ack')}
            >{t`Record acknowledgment`}</Button>
          )}
        </Cell>
        <Cell label={labels[4]}>
          {!context.stock_visible ? (
            <MissingValue reason={t`Receipt is unavailable for your role`} />
          ) : line.receipts.length ? (
            line.receipts.map((receipt) => (
              <Stack key={receipt.event_id} gap={4}>
                <Text size='sm'>{procurementTime(receipt.recorded_at)}</Text>
                <Text size='xs'>
                  {receipt.actor.label ?? (
                    <MissingValue reason={t`Receipt actor is not recorded`} />
                  )}
                </Text>
                {receipt.snapshot.stocks.map((stock) => (
                  <Stack key={stock.stock_item} gap={2}>
                    <Anchor
                      component={Link}
                      to={getDetailUrl(ModelType.stockitem, stock.stock_item)}
                      size='sm'
                    >
                      {t`Stock ${stock.stock_item}`} · {stock.quantity}
                      {line.base_uom ? ` ${line.base_uom}` : ''}
                    </Anchor>
                    <Text size='sm'>
                      {stock.batch || (
                        <MissingValue reason={t`Batch is not recorded`} />
                      )}
                    </Text>
                    <Anchor
                      component={Link}
                      to={getDetailUrl(ModelType.stocklocation, stock.location)}
                      size='sm'
                    >{t`Location ${stock.location}`}</Anchor>
                  </Stack>
                ))}
              </Stack>
            ))
          ) : (
            <MissingValue reason={t`No physical receipt recorded`} />
          )}
          {canEdit &&
            line.orders
              .filter(
                (order) =>
                  order.status === statusCodes.PLACED &&
                  Number(order.received_quantity) < Number(order.order_quantity)
              )
              .map((order) => (
                <Anchor
                  key={order.purchase_order_line_id}
                  component={Link}
                  to={`${getDetailUrl(ModelType.purchaseorder, order.purchase_order_id)}line-items/?workOrder=${context.work_order_id}`}
                  size='sm'
                >{t`Receive · ${order.reference}`}</Anchor>
              ))}
        </Cell>
        <Cell label={labels[5]}>
          {context.stock_visible ? (
            <>
              {Number(line.reserved_quantity) > 0 && (
                <Text size='sm'>
                  {t`Reserved`} {line.reserved_quantity}
                  {line.base_uom ? ` ${line.base_uom}` : ''}
                </Text>
              )}
              {Number(line.issued_quantity) > 0 && (
                <Text size='sm'>
                  {t`Issued`} {line.issued_quantity}
                  {line.base_uom ? ` ${line.base_uom}` : ''}
                </Text>
              )}
              {Number(line.used_quantity) > 0 && (
                <Text size='sm'>
                  {t`Used`} {line.used_quantity}
                  {line.base_uom ? ` ${line.base_uom}` : ''}
                </Text>
              )}
              {Number(line.outstanding_quantity) > 0 && (
                <Text
                  size='sm'
                  c='light-dark(var(--mantine-color-orange-9), var(--mantine-color-orange-3))'
                >
                  {t`Short`} {line.outstanding_quantity}
                  {line.base_uom ? ` ${line.base_uom}` : ''}
                </Text>
              )}
              {requirement?.reservations
                ?.filter((reservation) =>
                  ['reserved', 'staged', 'issued'].includes(reservation.status)
                )
                .map((reservation) => (
                  <Stack key={reservation.id} gap={2}>
                    {reservation.stock_item?.pk &&
                      !reservation.stock_item.hidden && (
                        <Anchor
                          component={Link}
                          to={getDetailUrl(
                            ModelType.stockitem,
                            reservation.stock_item.pk
                          )}
                          size='sm'
                        >{t`Stock ${reservation.stock_item.pk}`}</Anchor>
                      )}
                    <Text size='sm'>
                      {[
                        reservation.stock_item?.batch,
                        reservation.stock_item?.location
                      ]
                        .filter(Boolean)
                        .join(' · ') || (
                        <MissingValue
                          reason={t`Batch and location are not available`}
                        />
                      )}
                    </Text>
                  </Stack>
                ))}
              {line.receipt_outcomes
                .filter((outcome) => outcome.reservation_state !== 'allocated')
                .map((outcome) => (
                  <ValueTooltip
                    key={outcome.receipt_id}
                    label={
                      outcome.reservation_code ||
                      t`Receipt retained; reservation continuation has not completed`
                    }
                  >
                    <StatusBadge
                      tone='warning'
                      color='orange'
                      size='sm'
                      multiline
                    >
                      {outcome.reservation_state === 'pending'
                        ? t`Reservation pending`
                        : t`Reservation needs attention`}
                    </StatusBadge>
                  </ValueTooltip>
                ))}
            </>
          ) : (
            <MissingValue
              reason={t`Stock custody is unavailable for your role`}
            />
          )}
        </Cell>
      </SimpleGrid>
      {details && (
        <details className={classes.details}>
          <summary>{t`Material record`}</summary>
          <div className={classes.detailContent}>{details}</div>
        </details>
      )}
      <Modal
        opened={!!action}
        onClose={() => setAction(null)}
        title={
          action === 'link'
            ? t`Link placed order`
            : action === 'source'
              ? t`Source selection`
              : action === 'ack'
                ? t`Supplier acknowledgment`
                : t`Named PO approval`
        }
        size='lg'
      >
        {stale && <Text size='sm'>{t`Refresh Parts before recording`}</Text>}
        <fieldset
          disabled={stale || context.pending_operations.length > 0}
          style={{ border: 0, padding: 0, margin: 0, minInlineSize: 0 }}
        >
          {action === 'link' && shortage && line.part_id && (
            <LinkPlacedOrder
              workId={context.work_order_id}
              partId={line.part_id}
              shortageId={shortage.id}
              onSaved={() => setAction(null)}
            />
          )}
          {action === 'source' && (
            <SourceRecordForm
              context={context}
              line={line}
              onSaved={() => setAction(null)}
            />
          )}
          {action === 'ack' && (
            <AcknowledgmentForm
              context={context}
              line={line}
              onSaved={() => setAction(null)}
            />
          )}
          {action === 'approval' && payload && (
            <NamedApproval
              workOrderId={context.work_order_id}
              scope={`work:${context.work_order_id}:${line.kit_line_id}:${line.source_selection?.event_id}`}
              payload={payload}
              summary={t`Purchase for Work ${context.work_order_id} · ${requirement?.part?.name ?? line.part_id ?? t`Part`}`}
              linkEndpoint={
                shortage
                  ? `/api/tasks/work-orders/${context.work_order_id}/job-kit/shortages/${shortage.id}/link-po/`
                  : undefined
              }
              onLinked={() => setAction(null)}
            />
          )}
        </fieldset>
      </Modal>
    </Paper>
  );
}
export function WorkProcurement({
  readout,
  stale,
  renderDetails
}: Readonly<{
  readout: MaterialReadout;
  stale: boolean;
  renderDetails: (requirement: MaterialRequirement) => ReactNode;
}>) {
  const context = readout.procurement!;
  const labels = [
    t`Requirement`,
    t`Source / offer`,
    t`Purchase order`,
    t`Document / acknowledgment`,
    t`Receipt`,
    t`Reservation`
  ];
  return (
    <Stack
      gap='xs'
      data-testid='work-procurement'
      className={procurementClasses.context}
    >
      <SimpleGrid cols={6} spacing='md' visibleFrom='lg' px='sm'>
        {labels.map((label) => (
          <Text key={label} size='xs' fw={600} c='dimmed'>
            {label}
          </Text>
        ))}
      </SimpleGrid>
      {context.pending_operations.length > 0 && (
        <Text
          size='sm'
          c='light-dark(var(--mantine-color-orange-9), var(--mantine-color-orange-3))'
          component='output'
        >{t`Procurement operation pending`}</Text>
      )}
      {context.lines.map((line) => {
        const requirement = readout.requirements.find(
          (entry) => entry.key === line.key
        );
        return (
          <ProcurementRow
            key={line.key}
            context={context}
            line={line}
            requirement={requirement}
            stale={stale}
            details={requirement ? renderDetails(requirement) : undefined}
          />
        );
      })}
    </Stack>
  );
}
