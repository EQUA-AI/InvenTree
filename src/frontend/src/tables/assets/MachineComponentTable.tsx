import { t } from '@lingui/core/macro';
import { Anchor, Badge, Box, Text, useMatches } from '@mantine/core';
import { useCallback, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { RefreshAction } from '../../components/buttons/RefreshAction';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { UserRoles } from '@lib/enums/Roles';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl } from '@lib/functions/Navigation';
import useTable from '@lib/hooks/UseTable';
import type { MachineComponent } from '@lib/types/Assets';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import {
  FailedValue,
  MissingValue,
  ValueTooltip
} from '../../components/items/ValueTooltip';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';
import { FittedItemFact } from '../../pages/assets/FittedIdentity';
import {
  REFERENCE_PARTS,
  referencePartLabel
} from '../../pages/assets/equipment/referenceParts';
import { useUserState } from '../../states/UserState';
import { ComponentProvenance } from './ComponentFacts';
import { ComponentSlotDetail } from './ComponentSlotDetail';
import classes from './MachineComponentTable.module.css';

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
  machine,
  registryHref
}: Readonly<{
  machine: { pk: number; name?: string; asset_type?: string };
  registryHref: string;
}>) {
  const table = useTable('machine-component');
  const phone = useMatches({ base: true, sm: false });
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

    const columns: TableColumn<MachineComponent>[] = [
      {
        accessor: 'machine_name',
        title: t`Equipment`,
        width: '8%',
        sortable: false,
        hidden: !station || phone,
        render: (record) =>
          record.machine === machine.pk ? (
            <Text size='sm'>{t`Station`}</Text>
          ) : (
            <Anchor
              className={classes.link}
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
        width: station ? '16%' : '20%',
        sortable: false,
        render: (record) => (
          <div className={classes.nameActions}>
            <Anchor
              className={classes.link}
              component='button'
              type='button'
              size='sm'
              ta='left'
              onClick={() => openSlot(record.pk)}
              aria-haspopup='dialog'
            >
              {record.name}
            </Anchor>
            {phone && (
              <details className={classes.phoneDetails}>
                <summary
                  aria-label={t`Details for ${record.name}`}
                >{t`Details`}</summary>
                <div className={classes.secondaryFacts}>
                  {[
                    ...(station ? ['machine_name'] : []),
                    'reference_position',
                    'fitted_item',
                    'provenance'
                  ].map((accessor) => {
                    const column = columns.find(
                      (item) => item.accessor === accessor
                    )!;
                    return (
                      <div
                        key={accessor}
                        className={classes.cell}
                        data-label={column.title}
                        data-field={accessor}
                      >
                        {column.render?.(record)}
                      </div>
                    );
                  })}
                </div>
              </details>
            )}
          </div>
        )
      },
      {
        // R6 P13: where this slot sits on the reference model (owner rule O11).
        accessor: 'reference_position',
        title: t`3D position`,
        hidden: phone,
        width: '10%',
        sortable: false,
        render: (record) => {
          // An older server does not say; that is not "not in the model".
          if (record.reference_position === undefined)
            return (
              <FailedValue
                reason={t`3D position could not be read`}
                label={t`3D position`}
              />
            );
          if (record.reference_position === null)
            return <MissingValue reason={t`Not in the model`} />;
          // A position this build does not know is still not "not in it".
          const position = REFERENCE_PARTS.find(
            (part) => part.id === record.reference_position
          );
          return position ? (
            <Text size='sm'>{referencePartLabel(position.id)}</Text>
          ) : (
            <ValueTooltip
              label={t`This recorded position is not recognized by this view.`}
            >
              {record.reference_position}
            </ValueTooltip>
          );
        }
      },
      {
        // A successful empty fit read differs from a withheld/unavailable read.
        accessor: 'fitted_item',
        title: t`Fitted item`,
        hidden: phone,
        width: '10%',
        sortable: false,
        render: (record) =>
          showStock && record.fitted?.current ? (
            <FittedItemFact fit={record.fitted.current} compact />
          ) : !showStock || record.fitted ? (
            <MissingValue
              reason={
                showStock
                  ? t`No verified fitted identity is recorded for this slot.`
                  : t`Fitted identity is unavailable for your role.`
              }
            />
          ) : (
            <FailedValue
              reason={t`Fitted identity could not be read.`}
              label={t`Fitted item`}
            />
          )
      },
      {
        accessor: 'part_name',
        title: t`Catalogue part`,
        width: station ? '16%' : '20%',
        sortable: false,
        render: (record) => (
          <>
            {linkParts ? (
              <Anchor
                className={classes.link}
                component={Link}
                to={getDetailUrl(ModelType.part, record.part)}
                size='sm'
              >
                {record.part_name}
              </Anchor>
            ) : (
              <Text size='sm'>{record.part_name}</Text>
            )}
          </>
        )
      },
      {
        accessor: 'status',
        title: t`Fit basis`,
        width: '8%',
        sortable: false,
        // Keep the explanation on focus/hover while the table stays compact.
        render: (record) => (
          <ValueTooltip
            focusable={record.status !== 'verified'}
            label={
              record.status === 'verified'
                ? t`Assignment verified`
                : t`Slot assignment not verified`
            }
            accessibleLabel={
              record.status === 'verified'
                ? t`Verified: assignment verified`
                : t`Draft: slot assignment not verified`
            }
            data-table-tooltip
            className={classes.fitTarget}
          >
            <Badge
              component='span'
              className={classes.fit}
              data-verified={record.status === 'verified'}
              size='sm'
              variant='outline'
              color={record.status === 'verified' ? 'green' : 'gray'}
            >
              {record.status === 'verified' ? t`Verified` : t`Draft`}
            </Badge>
          </ValueTooltip>
        )
      },
      {
        accessor: 'provenance',
        title: t`Source`,
        hidden: phone,
        width: '12%',
        sortable: false,
        render: (record) => <ComponentProvenance record={record} />
      },
      {
        accessor: 'open_work',
        title: t`Open work`,
        width: '10%',
        textAlign: 'right',
        sortable: false,
        render: (record) =>
          record.open_work === null || record.open_work === undefined ? (
            <FailedValue
              reason={t`Work could not be checked`}
              label={t`Open work`}
            />
          ) : record.open_work.count ? (
            <Text size='sm'>{record.open_work.count}</Text>
          ) : (
            <Text size='sm'>0</Text>
          )
      },
      {
        // In-stock quantity of the catalogue part - shared by every slot of
        // that part, a separate fact from anything fitted, and only for
        // readers who may see stock.
        accessor: 'stock',
        title: t`In stock`,
        width: '10%',
        textAlign: 'right',
        sortable: false,
        hidden: !showStock,
        render: (record) =>
          record.virtual ? (
            <MissingValue reason={t`Not stocked: a logical group`} />
          ) : !record.stock ? (
            <FailedValue
              reason={t`Catalogue stock could not be read`}
              label={t`Catalogue stock`}
            />
          ) : record.stock.quantity > 0 && linkParts ? (
            <Anchor
              className={classes.link}
              component={Link}
              to={`${getDetailUrl(ModelType.part, record.part)}stock`}
              aria-label={t`${record.stock.quantity} in stock`}
              size='sm'
            >
              {record.stock.quantity}
            </Anchor>
          ) : record.stock.quantity > 0 ? (
            <Text size='sm' aria-label={t`${record.stock.quantity} in stock`}>
              {record.stock.quantity}
            </Text>
          ) : (
            <Text size='sm'>0</Text>
          )
      }
    ];
    const visibleWidth = columns
      .filter((column) => !column.hidden)
      .reduce(
        (total, column) => total + Number.parseFloat(String(column.width)),
        0
      );
    return columns.map((column) => ({
      ...column,
      minWidth: 0,
      width: phone
        ? 'auto'
        : `${(Number.parseFloat(String(column.width)) / visibleWidth) * 100}%`,
      resizable: !phone,
      render: (record: MachineComponent) => (
        <div
          className={classes.cell}
          data-label={column.title}
          data-field={column.accessor}
        >
          {column.render?.(record)}
        </div>
      )
    }));
  }, [
    machine.pk,
    machine.name,
    station,
    linkParts,
    showStock,
    openSlot,
    phone
  ]);

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
      <Box className={classes.partsTable}>
        <InvenTreeTable<MachineComponent>
          url={`${apiUrl(ApiEndpoints.equipment_registry)}${machine.pk}/components/`}
          tableState={table}
          columns={tableColumns}
          props={{
            enableSearch: false,
            tableActions: [
              <Anchor
                className={classes.link}
                key='registry'
                component={Link}
                to={registryHref}
                size='sm'
              >
                {t`Equipment registry`}
              </Anchor>
            ],
            minHeight: 0,
            withColumnBorders: false,
            withRowBorders: !phone,
            controlSize: phone ? 44 : undefined,
            enablePagination: true,
            hideSinglePagePagination: true,
            refreshControl: (
              <RefreshAction
                loading={table.isLoading}
                onClick={() => {
                  table.refreshTable();
                  table.clearSelectedRecords();
                }}
              />
            ),
            enableRefresh: true,
            // The URL holds the open slot, not filters for this table.
            enableQueryFilters: false,
            tableFilters,
            // A row opens its slot, except from a link or button inside it.
            onRowClick: (row, _index, event) => {
              if (
                (event?.target as HTMLElement | null)?.closest(
                  'a, button, summary, details, [data-table-tooltip]'
                )
              )
                return;
              openSlot(row.pk);
            }
          }}
        />
      </Box>
    </>
  );
}
