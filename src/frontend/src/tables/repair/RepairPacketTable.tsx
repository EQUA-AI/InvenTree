import { t } from '@lingui/core/macro';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { RepairPacket } from '@lib/types/Repair';
import type { TableColumn } from '@lib/types/Tables';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';

/**
 * Table component for displaying Repair Packets.
 */
export function RepairPacketTable() {
  const table = useTable('repair-packet');

  const tableColumns: TableColumn<RepairPacket>[] = useMemo(() => {
    return [
      {
        accessor: 'reference',
        title: t`Reference`,
        sortable: true
      },
      {
        accessor: 'machine_name',
        title: t`Asset`,
        sortable: false
      },
      {
        accessor: 'symptom',
        title: t`Symptom`,
        sortable: false
      },
      {
        accessor: 'criticality',
        title: t`Criticality`,
        sortable: true
      },
      {
        accessor: 'status_label',
        title: t`Status`,
        sortable: true
      },
      {
        accessor: 'created_at',
        title: t`Created`,
        sortable: true
      }
    ];
  }, []);

  return (
    <InvenTreeTable<RepairPacket>
      url={apiUrl(ApiEndpoints.repair_packet_list)}
      tableState={table}
      columns={tableColumns}
      props={{
        modelType: ModelType.repairpacket,
        enableSearch: true,
        enablePagination: true,
        enableRefresh: true,
        enableColumnSwitching: true
      }}
    />
  );
}
