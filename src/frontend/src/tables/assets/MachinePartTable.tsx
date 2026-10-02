import { t } from '@lingui/core/macro';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { MachinePart } from '@lib/types/Assets';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import {
  CreatedAfterFilter,
  CreatedBeforeFilter,
  PartCategoryFilter
} from '../../components/tables/Filter';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';

/**
 * Table component for displaying parts installed on a machine.
 */
export function MachinePartTable({
  machineId
}: Readonly<{ machineId: number }>) {
  const table = useTable('machine-part');

  const tableFilters = useMemo<TableFilter[]>(
    () => [
      PartCategoryFilter(),
      {
        name: 'group',
        label: t`Group`,
        description: t`Filter by part group`,
        type: 'text'
      },
      CreatedAfterFilter(),
      CreatedBeforeFilter()
    ],
    []
  );

  const tableColumns: TableColumn<MachinePart>[] = useMemo(() => {
    return [
      {
        accessor: 'part_name',
        title: t`Part`,
        sortable: true
      },
      {
        accessor: 'quantity',
        title: t`Quantity`,
        sortable: true
      },
      {
        accessor: 'part_group',
        title: t`Group`,
        sortable: true
      },
      {
        accessor: 'notes',
        title: t`Notes`,
        sortable: false
      }
    ];
  }, []);

  return (
    <InvenTreeTable<MachinePart>
      url={apiUrl(ApiEndpoints.asset_machine_part_list)}
      tableState={table}
      columns={tableColumns}
      props={{
        enableSearch: true,
        enablePagination: true,
        enableRefresh: true,
        tableFilters,
        params: {
          machine: machineId
        }
      }}
    />
  );
}
