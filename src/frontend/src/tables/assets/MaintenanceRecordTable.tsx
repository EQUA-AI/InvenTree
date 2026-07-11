import { t } from '@lingui/core/macro';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { TableColumn } from '@lib/types/Tables';
import type { AssetMaintenanceRecord } from '@lib/types/Assets';
import useTable from '@lib/hooks/UseTable';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';

/**
 * Table component for displaying maintenance records for a machine.
 */
export function MaintenanceRecordTable({
  machineId
}: Readonly<{ machineId: number }>) {
  const table = useTable('maintenance-record');

  const tableColumns: TableColumn<AssetMaintenanceRecord>[] = useMemo(() => {
    return [
      {
        accessor: 'date',
        title: t`Date`,
        sortable: true
      },
      {
        accessor: 'summary',
        title: t`Summary`,
        sortable: true
      },
      {
        accessor: 'performed_by',
        title: t`Performed By`,
        sortable: false
      },
      {
        accessor: 'work_order_title',
        title: t`Work Order`,
        sortable: false
      },
      {
        accessor: 'details',
        title: t`Details`,
        sortable: false,
        defaultVisible: false
      }
    ];
  }, []);

  return (
    <InvenTreeTable<AssetMaintenanceRecord>
      url={apiUrl(ApiEndpoints.asset_maintenance_list)}
      tableState={table}
      columns={tableColumns}
      props={{
        enableSearch: true,
        enablePagination: true,
        enableRefresh: true,
        params: {
          machine: machineId
        }
      }}
    />
  );
}
