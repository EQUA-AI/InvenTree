import { t } from '@lingui/core/macro';
import { Anchor, Badge, Group, Stack, Text } from '@mantine/core';
import { useCallback, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import useTable from '@lib/hooks/UseTable';
import type { MachineComponent } from '@lib/types/Assets';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';
import { FittedItemFact } from '../../pages/assets/FittedIdentity';
import {
  REFERENCE_PARTS,
  referencePartLabel
} from '../../pages/assets/equipment/referenceParts';
import { useUserState } from '../../states/UserState';
import { ComponentSlotDetail } from './ComponentSlotDetail';

/**
 * The equipment registry's components for a pump - or, for a station, its own
 * and those of every pump under it.
 *
 * Read-only: a component is reviewed in the registry, where the tags it was
 * inferred from can be seen beside it. The registry answers in pages and
 * filters by status, and does neither search nor ordering, so neither is
 * offered here.
 */
export function MachineComponentTable({
  machine
}: Readonly<{
  machine: { pk: number; name?: string; asset_type?: string };
}>) {
  const table = useTable('machine-component');
  const user = useUserState();
  const station = machine.asset_type === 'pumphouse';
  const linkParts = user.hasViewRole(UserRoles.part);
  const showStock = user.hasViewRole(UserRoles.stock);
  // R6 P13: the open slot lives in the URL, so Back and a shared link return
  // to it; opening pushes history, closing replaces it.
  const [params, setParams] = useSearchParams();
  const slot = params.get('slot');
  const openSlot = useCallback(
    (pk: number | null) =>
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous);
          if (pk === null) next.delete('slot');
          else next.set('slot', String(pk));
          return next;
        },
        { replace: pk === null }
      ),
    [setParams]
  );

  const tableFilters = useMemo<TableFilter[]>(
    () => [
      {
        name: 'status',
        label: t`Status`,
        description: t`Filter by review status`,
        type: 'choice',
        choices: [
          { value: 'draft', label: t`Draft` },
          { value: 'verified', label: t`Verified` }
        ]
      }
    ],
    []
  );

  const tableColumns: TableColumn<MachineComponent>[] = useMemo(() => {
    // Under its station a pump goes by its own name: the page says the rest.
    const prefix = `${machine.name ?? ''} / `;

    return [
      {
        accessor: 'machine_name',
        title: t`Equipment`,
        sortable: false,
        hidden: !station,
        render: (record) =>
          record.machine === machine.pk ? (
            <Text size='sm'>{t`Station`}</Text>
          ) : (
            <Anchor
              component={Link}
              to={`${getDetailUrl(ModelType.assetmachine, record.machine)}parts`}
              size='sm'
            >
              {record.machine_name.startsWith(prefix)
                ? record.machine_name.slice(prefix.length)
                : record.machine_name}
            </Anchor>
          )
      },
      {
        accessor: 'name',
        title: t`Component`,
        sortable: false,
        render: (record) => (
          <Stack gap={0} align='flex-start'>
            <Anchor
              component='button'
              type='button'
              size='sm'
              ta='left'
              onClick={() => openSlot(record.pk)}
              aria-haspopup='dialog'
            >
              {record.name}
            </Anchor>
            <Text size='xs' c='dimmed'>
              {record.code}
            </Text>
          </Stack>
        )
      },
      {
        // R6 P13: where this slot sits on the reference model (owner rule O11).
        accessor: 'reference_position',
        title: t`3D position`,
        sortable: false,
        render: (record) => {
          // An older server does not say; that is not "not in the model".
          if (record.reference_position === undefined)
            return (
              <Text size='sm' c='dimmed'>
                {t`Not available`}
              </Text>
            );
          if (record.reference_position === null)
            return (
              <Text size='sm' c='dimmed'>
                {t`Not in the model`}
              </Text>
            );
          // A position this build does not know is still not "not in it".
          const position = REFERENCE_PARTS.find(
            (part) => part.id === record.reference_position
          );
          return (
            <Text size='sm' c={position ? undefined : 'dimmed'}>
              {position ? referencePartLabel(position.id) : t`Not available`}
            </Text>
          );
        }
      },
      {
        // A successful empty fit read differs from a withheld/unavailable read.
        accessor: 'fitted_item',
        title: t`Fitted item`,
        sortable: false,
        render: (record) =>
          showStock && record.fitted?.current ? (
            <FittedItemFact fit={record.fitted.current} compact />
          ) : (
            <Text
              size='sm'
              c='dimmed'
              title={
                showStock && record.fitted
                  ? t`No verified fitted identity is recorded for this slot.`
                  : t`Fitted identity could not be read.`
              }
            >
              {showStock && record.fitted ? t`Not recorded` : t`Not available`}
            </Text>
          )
      },
      {
        accessor: 'part_name',
        title: t`Catalogue part`,
        sortable: false,
        render: (record) => (
          <Group gap='xs' wrap='nowrap'>
            {linkParts ? (
              <Anchor
                component={Link}
                to={getDetailUrl(ModelType.part, record.part)}
                size='sm'
              >
                {record.part_name}
              </Anchor>
            ) : (
              <Text size='sm'>{record.part_name}</Text>
            )}
            {record.virtual && (
              <Badge size='sm' variant='light' color='gray'>
                {t`Logical group`}
              </Badge>
            )}
          </Group>
        )
      },
      {
        accessor: 'status',
        title: t`Fit basis`,
        sortable: false,
        // The review state, then what it means for the fit, in full: a
        // narrow column must not cut "not verified" off the end.
        render: (record) => (
          <Stack gap={2} align='flex-start'>
            <Badge
              size='sm'
              variant='light'
              color={record.status === 'verified' ? 'green' : 'yellow'}
            >
              {record.status === 'verified' ? t`Verified` : t`Draft`}
            </Badge>
            <Text size='xs' c='dimmed'>
              {record.status === 'verified'
                ? t`Assignment verified`
                : t`Slot assignment not verified`}
            </Text>
          </Stack>
        )
      },
      {
        accessor: 'provenance',
        title: t`Source`,
        sortable: false
      },
      {
        accessor: 'open_work',
        title: t`Open work`,
        sortable: false,
        render: (record) =>
          record.open_work === null || record.open_work === undefined ? (
            <Text size='sm' c='dimmed' title={t`Work could not be checked`}>
              {t`Not available`}
            </Text>
          ) : record.open_work.count ? (
            <Text size='sm'>{t`${record.open_work.count} open`}</Text>
          ) : (
            <Text size='sm' c='dimmed'>
              {t`None open`}
            </Text>
          )
      },
      {
        // In-stock quantity of the catalogue part - shared by every slot of
        // that part, a separate fact from anything fitted, and only for
        // readers who may see stock.
        accessor: 'stock',
        title: t`Catalogue stock`,
        sortable: false,
        hidden: !showStock,
        render: (record) =>
          record.virtual ? (
            <Text size='sm' c='dimmed'>
              {t`Not stocked`}
            </Text>
          ) : !record.stock ? (
            <Text size='sm' c='dimmed'>
              {t`Not available`}
            </Text>
          ) : record.stock.quantity > 0 && linkParts ? (
            <Anchor
              component={Link}
              to={`${getDetailUrl(ModelType.part, record.part)}stock`}
              size='sm'
            >
              {t`${record.stock.quantity} in stock`}
            </Anchor>
          ) : record.stock.quantity > 0 ? (
            <Text size='sm'>{t`${record.stock.quantity} in stock`}</Text>
          ) : (
            <Text size='sm' c='dimmed'>
              {t`None in stock`}
            </Text>
          )
      }
    ];
  }, [machine.pk, machine.name, station, linkParts, showStock, openSlot]);

  const records = table.records as MachineComponent[];
  const record = slot
    ? (records.find((row) => String(row.pk) === slot) ?? null)
    : null;

  return (
    <>
      <ComponentSlotDetail
        machine={machine}
        record={record}
        missingSlot={
          !!slot && !record && !table.isLoading && records.length > 0
        }
        linkParts={linkParts}
        showStock={showStock}
        onClose={() => openSlot(null)}
      />
      <InvenTreeTable<MachineComponent>
        url={`${apiUrl(ApiEndpoints.equipment_registry)}${machine.pk}/components/`}
        tableState={table}
        columns={tableColumns}
        props={{
          enableSearch: false,
          enablePagination: true,
          enableRefresh: true,
          // The URL holds the open slot, not filters for this table.
          enableQueryFilters: false,
          tableFilters,
          // A row opens its slot, except from a link or button inside it.
          onRowClick: (row, _index, event) => {
            if ((event?.target as HTMLElement | null)?.closest('a, button'))
              return;
            openSlot(row.pk);
          }
        }}
      />
    </>
  );
}
