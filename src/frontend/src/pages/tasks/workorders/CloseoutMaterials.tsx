import { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl } from '@lib/functions/Navigation';
import type { CloseoutPartUsageRow } from '@lib/types/Closeout';
import { t } from '@lingui/core/macro';
import { Anchor, Badge, Box, SimpleGrid, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  MissingValue,
  ValueTooltip
} from '../../../components/items/ValueTooltip';
import { formatCurrency } from '../../../defaults/formatters';

function Missing({ label }: Readonly<{ label: string }>) {
  return (
    <MissingValue label={label} reason={t`No authorized record supplied`} />
  );
}
function disposition(row: CloseoutPartUsageRow) {
  if (row.state !== 'reconciled')
    return row.state === 'blocked' ? t`Blocked` : t`Pending reconciliation`;
  switch (row.disposition) {
    case 'consumed':
      return t`Consumed`;
    case 'spare_installed':
      return t`Installation recorded`;
    case 'returned':
      return t`Returned`;
    case 'scrapped':
      return t`Scrapped`;
    case 'correction':
      return t`Corrected`;
    case 'dismissed':
      return t`Dismissed`;
    case 'serialized_manual':
      return t`Manual disposition`;
    default:
      return row.disposition;
  }
}
/** Existing Closeout rows with independent custody, purchasing and fit facts. */
export function CloseoutMaterials({
  rows
}: Readonly<{ rows: CloseoutPartUsageRow[] }>) {
  const labels = [
    t`Material`,
    t`Actual use`,
    t`Traceability`,
    t`Purchasing`,
    t`Evidence`
  ];
  return (
    <Stack gap={0} data-testid='closeout-materials'>
      <SimpleGrid
        cols={5}
        spacing='md'
        visibleFrom='md'
        px='sm'
        py='xs'
        bg='var(--mantine-color-default-hover)'
      >
        {labels.map((label) => (
          <Text key={label} size='sm' fw={600}>
            {label}
          </Text>
        ))}
      </SimpleGrid>
      {rows.map((row) => {
        const material = row.material;
        const part = material?.part;
        const stock = material?.stock_item;
        const stockId = stock?.hidden ? null : (stock?.pk ?? row.stock_item);
        const partId = part?.pk ?? row.part;
        const tracking = material?.stock_tracking_id ?? row.stock_tracking_id;
        const fit = material?.fitted_record;
        const receipts = material?.receipts ?? [];
        const orders = [
          ...new Map(
            receipts.map((receipt) => [receipt.purchase_order_id, receipt])
          ).values()
        ];
        const cell = (label: string, content: ReactNode) => (
          <Stack gap={4} style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
            <Text hiddenFrom='md' size='xs' c='dimmed'>
              {label}
            </Text>
            {content}
          </Stack>
        );
        return (
          <SimpleGrid
            key={row.id}
            data-testid={`closeout-material-${row.id}`}
            cols={{ base: 1, xs: 2, md: 5 }}
            spacing='md'
            p='sm'
            style={{
              borderBottom: '1px solid var(--mantine-color-default-border)'
            }}
          >
            {cell(
              labels[0],
              <>
                {part ? (
                  <Anchor
                    component={Link}
                    to={getDetailUrl(ModelType.part, part.pk)}
                    size='sm'
                    fw={600}
                  >
                    {part.name}
                  </Anchor>
                ) : partId ? (
                  <Anchor
                    component={Link}
                    to={getDetailUrl(ModelType.part, partId)}
                    size='sm'
                  >{t`Part ${partId}`}</Anchor>
                ) : (
                  <Missing label={t`Part`} />
                )}
                {part?.ipn && <Text size='sm'>{part.ipn}</Text>}
                {!part && row.candidate_text && (
                  <Text size='sm'>{row.candidate_text}</Text>
                )}
              </>
            )}
            {cell(
              labels[1],
              <>
                <Badge
                  w='fit-content'
                  maw='100%'
                  tt='none'
                  variant='light'
                  color={row.state === 'reconciled' ? 'gray' : 'orange'}
                  styles={{
                    label: { whiteSpace: 'normal' },
                    root: { height: 'auto', minHeight: 22 }
                  }}
                >
                  {disposition(row)}
                </Badge>
                <Text size='sm'>
                  {t`Used`}:{' '}
                  {row.used_quantity == null ? (
                    <Missing label={t`Used quantity`} />
                  ) : (
                    `${row.used_quantity}${part?.units ? ` ${part.units}` : ''}`
                  )}
                </Text>
                <Text size='sm'>
                  {t`Issued`}:{' '}
                  {row.issued_quantity == null ? (
                    <Missing label={t`Issued quantity`} />
                  ) : (
                    row.issued_quantity
                  )}
                </Text>
                {material?.issued?.by && (
                  <ValueTooltip
                    label={`${material.issued.by} · ${material.issued.at ?? ''}`}
                  >
                    <Text size='sm' span>{t`Issue record`}</Text>
                  </ValueTooltip>
                )}
              </>
            )}
            {cell(
              labels[2],
              <>
                {stockId ? (
                  <Anchor
                    component={Link}
                    to={getDetailUrl(ModelType.stockitem, stockId!)}
                    size='sm'
                  >{t`Stock ${stockId}`}</Anchor>
                ) : (
                  <Missing label={t`Stock`} />
                )}
                <Text size='sm'>
                  {t`Batch`}: {stock?.batch || <Missing label={t`Batch`} />}
                </Text>
                {stock?.serial && (
                  <Text size='sm'>
                    {t`Serial`}: {stock.serial}
                  </Text>
                )}
                {stockId && tracking ? (
                  <Anchor
                    component={Link}
                    to={`${getDetailUrl(ModelType.stockitem, stockId!)}tracking/`}
                    size='sm'
                  >{t`Use tracking ${tracking}`}</Anchor>
                ) : (
                  <Missing label={t`Use tracking`} />
                )}
              </>
            )}
            {cell(
              labels[3],
              orders.length ? (
                orders.map((order) => (
                  <Box key={order.purchase_order_id}>
                    <Anchor
                      component={Link}
                      to={getDetailUrl(
                        ModelType.purchaseorder,
                        order.purchase_order_id
                      )}
                      size='sm'
                    >
                      {order.reference}
                    </Anchor>
                    <Text size='sm'>
                      {t`PO total`}:{' '}
                      {order.order_total == null ? (
                        <Missing label={t`PO total`} />
                      ) : (
                        <ValueTooltip
                          label={`${t`Goods`}: ${order.goods_total ?? '—'} · ${t`Additional charges`}: ${order.charges_total ?? '—'}`}
                        >
                          <span style={{ fontVariantNumeric: 'tabular-nums' }}>
                            {formatCurrency(order.order_total, {
                              currency: order.currency,
                              minDigits: 2,
                              digits: 6
                            })}
                          </span>
                        </ValueTooltip>
                      )}
                    </Text>
                  </Box>
                ))
              ) : (
                <Missing label={t`Purchasing`} />
              )
            )}
            {cell(
              labels[4],
              <>
                {receipts.map((receipt) => (
                  <ValueTooltip
                    key={receipt.event_id}
                    focusable={false}
                    label={`${receipt.actor.label ?? '—'} · ${receipt.recorded_at}\n${receipt.stocks.map((item) => `${item.quantity} · ${item.batch}`).join('\n')}`}
                  >
                    <Anchor
                      component={Link}
                      to={'?work_panel=history'}
                      size='sm'
                    >{t`Receipt ${receipt.event_id}`}</Anchor>
                  </ValueTooltip>
                ))}
                {!receipts.length && <Missing label={t`Receipt`} />}
                {fit ? (
                  <details>
                    <summary>
                      {fit.retracted_at
                        ? t`Retracted fit record`
                        : fit.verified_at
                          ? t`Verified fit record`
                          : t`Pending fit record`}
                    </summary>
                    <Stack gap={3} mt={4}>
                      <Text size='sm'>{fit.reason}</Text>
                      <Text size='sm'>
                        {fit.recorded_by} · {fit.recorded_at}
                      </Text>
                      <Anchor
                        component={Link}
                        to={getDetailUrl(ModelType.stockitem, fit.stock_item)}
                        size='sm'
                      >{t`Stock ${fit.stock_item}`}</Anchor>
                    </Stack>
                  </details>
                ) : (
                  <Missing label={t`Fit evidence`} />
                )}
              </>
            )}
          </SimpleGrid>
        );
      })}
    </Stack>
  );
}
