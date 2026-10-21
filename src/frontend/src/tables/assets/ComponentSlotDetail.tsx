import { t } from '@lingui/core/macro';
import {
  Anchor,
  Badge,
  Divider,
  Drawer,
  Group,
  Stack,
  Text,
  Title
} from '@mantine/core';
import type { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';

import { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl } from '@lib/functions/Navigation';
import type { MachineComponent } from '@lib/types/Assets';
import { returnState } from '../../components/nav/returnPath';
import { FittedIdentity } from '../../pages/assets/FittedIdentity';
import {
  REFERENCE_PARTS,
  referencePartLabel
} from '../../pages/assets/equipment/referenceParts';

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

const missing = (text: string) => (
  <Text size='sm' c='dimmed'>
    {text}
  </Text>
);

/**
 * R6 P13: one slot's detail, opened from the Parts table (`?slot=<pk>`).
 *
 * Position, what is (not) recorded as fitted, the catalogue part and its fit
 * basis, the slot's open work and the catalogue part's stock, each as its own
 * fact. Native records open with a labelled way back here.
 */
export function ComponentSlotDetail({
  machine,
  record,
  missingSlot,
  linkParts,
  showStock,
  onClose
}: Readonly<{
  machine: { pk: number; name?: string; asset_type?: string };
  record: MachineComponent | null;
  missingSlot: boolean;
  linkParts: boolean;
  showStock: boolean;
  onClose: () => void;
}>) {
  const location = useLocation();
  const back = returnState(
    `${location.pathname}${location.search}`,
    t`${machine.name ?? t`machine`} · Parts`
  );
  const opened = !!record || missingSlot;
  const ownMachine = record?.machine === machine.pk;
  const position =
    record?.reference_position &&
    REFERENCE_PARTS.find((part) => part.id === record.reference_position);

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      position='right'
      size='md'
      title={
        <Title order={4} component='h2'>
          {record ? record.name : t`Component`}
        </Title>
      }
      aria-label={t`Component slot`}
    >
      {!record ? (
        missingSlot && (
          <Text size='sm' c='dimmed'>
            {t`This component is not in the list shown. It may be on another page or no longer held by the registry.`}
          </Text>
        )
      ) : (
        <Stack gap='md' data-slot-detail={record.pk}>
          <Text size='xs' c='dimmed'>
            {record.code}
          </Text>
          <Fact label={t`Position`}>
            <Text size='sm'>
              {ownMachine
                ? (machine.name ?? t`This machine`)
                : record.machine_name}
            </Text>
            {position ? (
              <Group gap='xs'>
                <Text size='sm'>{referencePartLabel(position.id)}</Text>
                <Anchor
                  component={Link}
                  to={`${getDetailUrl(ModelType.assetmachine, record.machine)}equipment/?position=${position.id}`}
                  size='sm'
                >
                  {t`View in 3D`}
                </Anchor>
              </Group>
            ) : (
              missing(
                record.reference_position === undefined ||
                  record.reference_position
                  ? t`3D position not available`
                  : t`Not in the 3D model`
              )
            )}
          </Fact>
          <Fact label={t`Fitted item`}>
            <FittedIdentity
              key={record.pk}
              machineId={record.machine}
              slotId={record.pk}
              value={record.fitted}
            />
          </Fact>
          <Fact label={t`Catalogue part`}>
            <Group gap='xs'>
              <Text size='sm'>{record.part_name}</Text>
              {record.virtual && (
                <Badge size='sm' variant='light' color='gray'>
                  {t`Logical group`}
                </Badge>
              )}
            </Group>
            <Text size='xs' c='dimmed'>
              {t`The family this slot belongs to - not what is fitted.`}
            </Text>
          </Fact>
          <Fact label={t`Fit basis`}>
            <Group gap='xs'>
              <Badge
                size='sm'
                variant='light'
                color={record.status === 'verified' ? 'green' : 'yellow'}
              >
                {record.status === 'verified' ? t`Verified` : t`Draft`}
              </Badge>
              <Text size='sm'>
                {record.status === 'verified'
                  ? t`Assignment verified`
                  : t`Slot assignment not verified`}
              </Text>
            </Group>
            <Text size='sm'>{record.provenance}</Text>
            {record.review_note && (
              <Text size='sm' c='dimmed'>
                {record.review_note}
              </Text>
            )}
          </Fact>
          <Fact label={t`Work`}>
            {record.open_work === null || record.open_work === undefined ? (
              missing(t`Open work could not be checked.`)
            ) : (
              <Text size='sm'>
                {record.open_work.count
                  ? t`${record.open_work.count} open job(s) name this slot.`
                  : t`No open job names this slot.`}
              </Text>
            )}
            {missing(t`Material requirements are not recorded per slot.`)}
            <Anchor
              component={Link}
              to={`${getDetailUrl(ModelType.assetmachine, record.machine)}maintenance`}
              size='sm'
            >
              {t`Open Maintenance`}
            </Anchor>
          </Fact>
          {showStock && (
            <Fact label={t`Catalogue stock`}>
              {record.virtual ? (
                missing(t`Not stocked: a logical group.`)
              ) : !record.stock ? (
                missing(t`Not available`)
              ) : record.stock.quantity > 0 ? (
                <Text size='sm'>
                  {t`${record.stock.quantity} in stock for this catalogue part, shared by every slot that uses it.`}
                </Text>
              ) : (
                missing(t`None in stock for this catalogue part.`)
              )}
            </Fact>
          )}
          <Divider />
          {linkParts ? (
            <Group gap='md'>
              <Anchor
                component={Link}
                to={getDetailUrl(ModelType.part, record.part)}
                state={back}
                size='sm'
              >
                {t`Open part record`}
              </Anchor>
              {showStock && !record.virtual && (
                <Anchor
                  component={Link}
                  to={`${getDetailUrl(ModelType.part, record.part)}stock`}
                  state={back}
                  size='sm'
                >
                  {t`Open stock`}
                </Anchor>
              )}
            </Group>
          ) : (
            missing(t`Part records are not available to you.`)
          )}
        </Stack>
      )}
    </Drawer>
  );
}
