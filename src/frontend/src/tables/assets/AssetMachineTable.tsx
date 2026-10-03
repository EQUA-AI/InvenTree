import { t } from '@lingui/core/macro';
import { Badge, Group, Text } from '@mantine/core';
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
      },
      {
        // Where there is something to look at this morning. A station's
        // count carries its pumps', so the stations can be read on their own.
        accessor: 'open_alarms',
        title: t`Alarms`,
        sortable: false,
        render: (record: AssetMachine) => {
          const open = record.open_alarms ?? 0;
          const critical = record.open_critical_alarms ?? 0;
          if (!open) {
            return (
              <Text size='sm' c='dimmed'>
                —
              </Text>
            );
          }
          return (
            <Group gap={6} wrap='nowrap'>
              {critical > 0 && (
                <Badge color='red' variant='filled' size='sm'>
                  {t`${critical} critical`}
                </Badge>
              )}
              {open > critical && (
                <Badge color='yellow' variant='light' size='sm'>
                  {t`${open - critical} warning`}
                </Badge>
              )}
            </Group>
          );
        }
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
