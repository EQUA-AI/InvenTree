import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Paper,
  Skeleton,
  Stack,
  Text
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import { api } from '../../../App';
import { returnState } from '../../../components/nav/returnPath';
import { useUserState } from '../../../states/UserState';
import {
  type MaterialReadout,
  type MaterialRequirement,
  type MaterialReservation,
  type MaterialStock,
  type MaterialUsage,
  OPEN_SHORTAGE,
  type Stage,
  coveredByStock,
  reservationStages,
  reservedQuantity
} from './materials';

const when = (at: string | null) =>
  at ? new Date(at).toLocaleString() : t`time not recorded`;

const amount = (quantity: number | null, units: string | undefined) =>
  quantity === null
    ? t`not recorded`
    : `${quantity}${units ? ` ${units}` : ''}`;

function stageLabel(stage: Stage) {
  switch (stage.key) {
    case 'reserved':
      return t`Reserved`;
    case 'issued':
      return t`Issued`;
    case 'used':
      return t`Used`;
    case 'returned':
      return t`Returned unused`;
    case 'released':
      return t`Released`;
  }
}

/** Reserved → Issued → Used, each with who and when, or that it is not recorded. */
function Stages({ stages }: Readonly<{ stages: Stage[] }>) {
  return (
    <Group
      gap='xs'
      wrap='wrap'
      component='ol'
      style={{ listStyle: 'none', padding: 0, margin: 0 }}
    >
      {stages.map((stage, index) => (
        <Group key={stage.key} gap={6} wrap='nowrap' component='li'>
          {index > 0 && (
            <Text size='xs' c='dimmed' aria-hidden>
              →
            </Text>
          )}
          <Stack gap={0}>
            <Badge
              size='sm'
              variant={stage.state === 'done' ? 'light' : 'outline'}
              color={stage.state === 'done' ? 'blue' : 'gray'}
            >
              {stageLabel(stage)}
            </Badge>
            <Text size='xs' c='dimmed'>
              {stage.state === 'done'
                ? `${stage.by ?? t`who not recorded`} · ${when(stage.at)}`
                : stage.state === 'skipped'
                  ? t`not recorded`
                  : t`not yet`}
            </Text>
          </Stack>
        </Group>
      ))}
    </Group>
  );
}

function StockLabel({
  stock,
  linkStock,
  back
}: Readonly<{
  stock: MaterialStock | null;
  linkStock: boolean;
  back: ReturnType<typeof returnState>;
}>) {
  if (!stock) return <Text size='sm'>{t`No stock item recorded`}</Text>;
  if (stock.hidden || stock.pk === null)
    return (
      <Text size='sm' c='dimmed'>
        {t`Stock item not available to you`}
      </Text>
    );
  const identity = [
    stock.serial && t`Serial ${stock.serial}`,
    stock.batch && t`Batch ${stock.batch}`,
    stock.location || t`location not recorded`
  ]
    .filter(Boolean)
    .join(' · ');
  return linkStock ? (
    <Anchor
      component={Link}
      to={getDetailUrl(ModelType.stockitem, stock.pk)}
      state={back}
      size='sm'
    >
      {t`Stock item ${stock.pk}`} · {identity}
    </Anchor>
  ) : (
    <Text size='sm'>
      {t`Stock item ${stock.pk}`} · {identity}
    </Text>
  );
}

function UsageLine({
  usage,
  units
}: Readonly<{ usage: MaterialUsage; units?: string }>) {
  return (
    <Text size='xs' c='dimmed'>
      {t`Closeout record`}: {t`issued`} {amount(usage.issued_quantity, units)},{' '}
      {t`used`} {amount(usage.used_quantity, units)}
      {usage.disposition ? ` · ${usage.disposition.replaceAll('_', ' ')}` : ''}
      {` · ${usage.state}`}
      {usage.resolved_by ? ` · ${t`resolved by`} ${usage.resolved_by}` : ''}
      {usage.variance_reason ? ` · ${usage.variance_reason}` : ''}
    </Text>
  );
}

function Reservation({
  reservation,
  units,
  linkStock,
  back
}: Readonly<{
  reservation: MaterialReservation;
  units?: string;
  linkStock: boolean;
  back: ReturnType<typeof returnState>;
}>) {
  return (
    <Stack gap={4} data-reservation={reservation.id}>
      <Group gap='xs' wrap='wrap'>
        <Text size='sm' fw={600}>
          {amount(reservation.quantity, units)}
        </Text>
        <StockLabel
          stock={reservation.stock_item}
          linkStock={linkStock}
          back={back}
        />
      </Group>
      <Stages stages={reservationStages(reservation)} />
      {reservation.usage.map((usage) => (
        <UsageLine key={usage.id} usage={usage} units={units} />
      ))}
    </Stack>
  );
}

function Fact({
  label,
  children
}: Readonly<{ label: string; children: ReactNode }>) {
  return (
    <Stack gap={2}>
      <Text size='xs' c='dimmed' fw={600}>
        {label}
      </Text>
      {children}
    </Stack>
  );
}

function Requirement({
  requirement,
  linkParts,
  linkStock,
  back
}: Readonly<{
  requirement: MaterialRequirement;
  linkParts: boolean;
  linkStock: boolean;
  back: ReturnType<typeof returnState>;
}>) {
  const part = requirement.part;
  const units = part?.units;
  const reserved = reservedQuantity(requirement);
  const open = (requirement.shortages ?? []).filter((shortage) =>
    OPEN_SHORTAGE.includes(shortage.status)
  );
  return (
    <Paper
      withBorder
      p='sm'
      radius='sm'
      component='li'
      data-requirement={requirement.key}
    >
      <Stack gap='sm'>
        <Group justify='space-between' wrap='wrap' gap='xs'>
          <Stack gap={0}>
            <Text fw={600}>{part?.name ?? t`Part not recorded`}</Text>
            {part?.ipn && (
              <Text size='xs' c='dimmed'>
                {part.ipn}
              </Text>
            )}
          </Stack>
          <Group gap='xs'>
            <Text size='sm'>
              {t`Required`} {amount(requirement.quantity, units)}
            </Text>
            {!requirement.required && (
              <Badge size='sm' variant='outline' color='gray'>
                {t`Optional`}
              </Badge>
            )}
          </Group>
        </Group>
        {requirement.requested_part && (
          <Text size='xs' c='dimmed'>
            {t`Selected in place of ${requirement.requested_part.name}`}
          </Text>
        )}
        <Fact label={t`Fit basis`}>
          {requirement.fit_basis ? (
            <Text size='sm'>
              {t`Source: ${requirement.fit_basis.source}`}
              {requirement.fit_basis.note
                ? ` · ${requirement.fit_basis.note}`
                : ''}
            </Text>
          ) : (
            <Text size='sm' c='dimmed'>
              {t`Not recorded for this requirement`}
            </Text>
          )}
        </Fact>
        <Fact label={t`Stock for this job`}>
          {requirement.reservations === null ? (
            <Stack gap={2}>
              <Text size='sm' c='dimmed'>
                {t`Not reserved: no stock item is reserved to this requirement.`}
              </Text>
              {requirement.availability_check && (
                <Text size='xs' c='dimmed'>
                  {t`Stock check (on-hand count, not a reservation)`}:{' '}
                  {requirement.availability_check.note ||
                    requirement.availability_check.status}{' '}
                  · {when(requirement.availability_check.checked_at)}
                </Text>
              )}
            </Stack>
          ) : requirement.reservations.length === 0 ? (
            <Text size='sm' c='dimmed'>
              {t`Nothing reserved yet`}
            </Text>
          ) : (
            <Stack gap='xs'>
              <Text size='xs' c='dimmed'>
                {t`Reserved ${amount(reserved, units)} of ${amount(requirement.quantity, units)}`}
              </Text>
              <Stack
                gap='xs'
                component='ul'
                style={{ listStyle: 'none', padding: 0, margin: 0 }}
              >
                {requirement.reservations.map((reservation) => (
                  <li key={reservation.id}>
                    <Reservation
                      reservation={reservation}
                      units={units}
                      linkStock={linkStock}
                      back={back}
                    />
                  </li>
                ))}
              </Stack>
            </Stack>
          )}
        </Fact>
        {open.length > 0 && (
          <Alert color='orange' variant='light' p='xs' title={t`Short`}>
            {open.map((shortage) => (
              <Text size='sm' key={shortage.id}>
                {amount(shortage.quantity, units)} · {shortage.status}
                {shortage.purchase_order_line
                  ? ` · ${t`purchase order line ${shortage.purchase_order_line}`}`
                  : ` · ${t`no purchase order linked`}`}
                {shortage.reason ? ` · ${shortage.reason}` : ''}
              </Text>
            ))}
          </Alert>
        )}
        {part && linkParts && (
          <Group gap='md'>
            <Anchor
              component={Link}
              to={getDetailUrl(ModelType.part, part.pk)}
              state={back}
              size='sm'
              aria-label={t`Open part record: ${part.name}`}
            >
              {t`Open part record`}
            </Anchor>
          </Group>
        )}
      </Stack>
    </Paper>
  );
}

/**
 * R6 P22: Parts for this job - requirement, reservation, issue and recorded
 * use, each from its own record. Falls back to the native requirement table
 * where the server has no material readout.
 */
export function WorkMaterialsPanel({
  workOrderId,
  label,
  fallback
}: Readonly<{ workOrderId: number; label: string; fallback: ReactNode }>) {
  const user = useUserState();
  const location = useLocation();
  const back = returnState(
    `${location.pathname}${location.search}`,
    t`${label} · Parts`
  );
  const linkParts = user.hasViewRole(UserRoles.part);
  const linkStock = user.hasViewRole(UserRoles.stock);
  const query = useQuery({
    queryKey: ['work-order-materials', workOrderId],
    retry: false,
    queryFn: async ({ signal }) =>
      (
        await api.get<MaterialReadout>(
          apiUrl(ApiEndpoints.work_order_materials, workOrderId),
          { signal }
        )
      ).data
  });

  if (query.isPending)
    return (
      <Stack gap='sm' aria-busy>
        <Skeleton height={20} width='40%' />
        <Skeleton height={96} />
      </Stack>
    );

  if (query.isError) {
    const status = (query.error as { response?: { status?: number } })?.response
      ?.status;
    if (status === 404)
      return (
        <Stack gap='sm'>
          <Text size='sm' c='dimmed'>
            {t`Detailed material records are not available on this server; the job's required parts are shown as recorded.`}
          </Text>
          {fallback}
        </Stack>
      );
    return (
      <Alert
        color='red'
        variant='light'
        title={t`Parts for this job could not be read`}
      >
        <Stack gap='xs' align='flex-start'>
          <Text size='sm'>{t`Nothing is shown rather than an old or partial list.`}</Text>
          <Button size='xs' variant='light' onClick={() => query.refetch()}>
            {t`Retry`}
          </Button>
        </Stack>
      </Alert>
    );
  }

  const readout = query.data;
  const component = readout.component;
  return (
    <Stack gap='md' data-material-source={readout.source}>
      <Group justify='space-between' wrap='wrap'>
        <Text size='sm' c='dimmed' data-requirement-count>
          {readout.requirements.length === 1
            ? t`1 requirement`
            : t`${readout.requirements.length} requirements`}
        </Text>
        {coveredByStock(readout) && (
          <Text size='sm' c='dimmed' data-covered>
            {t`Available stock used for this job`}
          </Text>
        )}
      </Group>
      {component && (
        <Text size='sm'>
          {t`Component`}:{' '}
          {component.pk && component.machine ? (
            <Anchor
              component={Link}
              to={`${getDetailUrl(ModelType.assetmachine, component.machine)}parts/?slot=${component.pk}`}
              size='sm'
            >
              {component.name || component.ref}
            </Anchor>
          ) : (
            <>
              {component.ref}{' '}
              <Text span size='xs' c='dimmed'>
                ({t`not in the machine's registry`})
              </Text>
            </>
          )}
        </Text>
      )}
      {readout.requirements.length === 0 ? (
        <Text size='sm' c='dimmed'>
          {t`No parts are required by this job.`}
        </Text>
      ) : (
        <Stack
          gap='sm'
          component='ul'
          style={{ listStyle: 'none', padding: 0, margin: 0 }}
        >
          {readout.requirements.map((requirement) => (
            <Requirement
              key={requirement.key}
              requirement={requirement}
              linkParts={linkParts}
              linkStock={linkStock && readout.stock_visible}
              back={back}
            />
          ))}
        </Stack>
      )}
      {readout.unmatched_usage.length > 0 && (
        <Stack gap={4}>
          <Text size='xs' c='dimmed' fw={600}>
            {t`Use recorded at closeout without a reservation`}
          </Text>
          {readout.unmatched_usage.map((usage) => (
            <Stack gap={0} key={usage.id}>
              <Text size='sm'>
                {usage.part?.name ??
                  (usage.candidate_text || t`Part not identified`)}
              </Text>
              <UsageLine usage={usage} units={usage.part?.units} />
            </Stack>
          ))}
        </Stack>
      )}
      <Text size='xs' c='dimmed'>
        {t`Fitted on the machine is not recorded separately from use.`}
      </Text>
    </Stack>
  );
}
