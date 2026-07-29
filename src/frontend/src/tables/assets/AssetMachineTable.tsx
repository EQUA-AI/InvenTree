import { t } from '@lingui/core/macro';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { AssetMachine } from '@lib/types/Assets';
import type { TableColumn } from '@lib/types/Tables';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';

/**
 * Table component for displaying Asset Machines.
 */
export function AssetMachineTable() {
  const table = useTable('asset-machine');

  const tableColumns: TableColumn<AssetMachine>[] = useMemo(() => {
    return [
      {
        accessor: 'name',
        title: t`Name`,
        sortable: true
      },
      {
        accessor: 'location',
        title: t`Location`,
        sortable: true
      },
      {
        accessor: 'manufacturer',
        title: t`Manufacturer`,
        sortable: true
      },
      {
        accessor: 'model',
        title: t`Model`,
        sortable: true
      },
      {
        accessor: 'serial',
        title: t`Serial`,
        sortable: false
      },
      {
        accessor: 'active',
        title: t`Active`,
        sortable: true,
        render: (record: AssetMachine) => (record.active ? t`Yes` : t`No`)
      }
    ];
  }, []);

  return (
    <InvenTreeTable<AssetMachine>
      url={apiUrl(ApiEndpoints.asset_machine_list)}
      tableState={table}
      columns={tableColumns}
      props={{
        modelType: ModelType.assetmachine,
        enableSearch: true,
        enablePagination: true,
        enableRefresh: true,
        enableColumnSwitching: true
      }}
    />
  );
}
