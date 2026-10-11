import { t } from '@lingui/core/macro';
import { Anchor, Badge, Group, Stack, Text } from '@mantine/core';
import { useMemo } from 'react';
import { Link } from 'react-router-dom';

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
import { useUserState } from '../../states/UserState';

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
          <Stack gap={0}>
            <Text size='sm'>{record.name}</Text>
            <Text size='xs' c='dimmed'>
              {record.code}
            </Text>
          </Stack>
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
        title: t`Status`,
        sortable: false,
        render: (record) => (
          <Badge
            size='sm'
            variant='light'
            color={record.status === 'verified' ? 'green' : 'yellow'}
          >
            {record.status === 'verified' ? t`Verified` : t`Draft`}
          </Badge>
        )
      },
      {
        accessor: 'provenance',
        title: t`Basis`,
        sortable: false
      }
    ];
  }, [machine.pk, machine.name, station, linkParts]);

  return (
    <InvenTreeTable<MachineComponent>
      url={`${apiUrl(ApiEndpoints.equipment_registry)}${machine.pk}/components/`}
      tableState={table}
      columns={tableColumns}
      props={{
        enableSearch: false,
        enablePagination: true,
        enableRefresh: true,
        tableFilters
      }}
    />
  );
}
