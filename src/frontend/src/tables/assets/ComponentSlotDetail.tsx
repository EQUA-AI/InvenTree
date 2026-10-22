import { t } from '@lingui/core/macro';
import {
  Anchor,
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
import { FailedValue, MissingValue } from '../../components/items/ValueTooltip';
import { returnState } from '../../components/nav/returnPath';
import { FittedIdentity } from '../../pages/assets/FittedIdentity';
import {
  REFERENCE_PARTS,
  referencePartLabel
} from '../../pages/assets/equipment/referenceParts';
import { ComponentBasis } from './ComponentFacts';

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

const missing = (reason: string) => <MissingValue reason={reason} />;

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
                  to={`${getDetailUrl(ModelType.assetmachine, record.machine)}equipment/?view=model&position=${position.id}`}
                  size='sm'
                >
                  {t`View in 3D`}
                </Anchor>
              </Group>
            ) : record.reference_position === undefined ? (
              <FailedValue
                label={t`3D position`}
                reason={t`3D position could not be read`}
              />
            ) : (
              missing(
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
            </Group>
          </Fact>
          <Fact label={t`Fit basis`}>
            <ComponentBasis record={record} />
          </Fact>
          <Fact label={t`Work`}>
            {record.open_work === null || record.open_work === undefined ? (
              <FailedValue
                reason={t`Open work could not be checked.`}
                label={t`Open work`}
              />
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
                <FailedValue
                  reason={t`Catalogue stock could not be read`}
                  label={t`Catalogue stock`}
                />
              ) : record.stock.quantity > 0 ? (
                <Text size='sm'>
                  {t`${record.stock.quantity} in stock for this catalogue part, shared by every slot that uses it.`}
                </Text>
              ) : (
                <Text size='sm'>0</Text>
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
