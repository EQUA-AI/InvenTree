import { t } from '@lingui/core/macro';
import { Anchor, Badge, Group, Text } from '@mantine/core';
import { IconAlertTriangle, IconCircleCheck } from '@tabler/icons-react';
import { useMemo } from 'react';
import { Link } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { AssetMaintenanceRecord } from '@lib/types/Assets';
import type { TableColumn } from '@lib/types/Tables';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';

const WORK_ORDER_TYPE_LABELS: Record<string, () => string> = {
  corrective: () => t`Corrective`,
  preventive: () => t`Preventive`,
  inspection: () => t`Inspection`,
  calibration: () => t`Calibration`,
  other: () => t`Other`
};

function formatDowntime(minutes: number | null) {
  if (minutes == null) {
    return '';
  }
  if (minutes < 60) {
    return t`${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder === 0 ? t`${hours} h` : t`${hours} h ${remainder} min`;
}

/**
 * Maintenance blade for a machine: the durable job-history index.
 *
 * Each completed row links to the authoritative work order at
 * `/maintenance/work-orders/:id/`. Rows without a link are genuinely unowned
 * legacy history and are shown as such - never with a fabricated reference.
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
        accessor: 'work_order',
        title: t`Work Order`,
        sortable: false,
        render: (record: AssetMaintenanceRecord) => {
          if (!record.work_order) {
            return (
              <Text size='sm' c='dimmed'>
                {t`Legacy record`}
              </Text>
            );
          }

          const label = record.work_order_reference || t`Work order`;

          return (
            <Anchor
              component={Link}
              to={`/maintenance/work-orders/${record.work_order}/`}
              size='sm'
              aria-label={t`Open work order ${label}`}
              title={record.work_order_title ?? undefined}
            >
              {label}
            </Anchor>
          );
        }
      },
      {
        accessor: 'work_order_title',
        title: t`Job`,
        sortable: false,
        defaultVisible: false
      },
      {
        accessor: 'work_order_type',
        title: t`Type`,
        sortable: false,
        render: (record: AssetMaintenanceRecord) => {
          if (!record.work_order_type) {
            return '';
          }
          const label =
            WORK_ORDER_TYPE_LABELS[record.work_order_type]?.() ??
            record.work_order_type;
          return (
            <Badge variant='light' color='gray'>
              {label}
            </Badge>
          );
        }
      },
      {
        accessor: 'performed_by',
        title: t`Performed By`,
        sortable: false
      },
      {
        accessor: 'lifecycle_status',
        title: t`Outcome`,
        sortable: false,
        render: (record: AssetMaintenanceRecord) =>
          record.lifecycle_status === 'completed'
            ? t`Completed`
            : (record.lifecycle_status ?? '')
      },
      {
        accessor: 'downtime_minutes',
        title: t`Downtime`,
        sortable: false,
        render: (record: AssetMaintenanceRecord) =>
          formatDowntime(record.downtime_minutes)
      },
      {
        accessor: 'verified',
        title: t`Verified`,
        sortable: false,
        // Icon plus text: severity and state must not rely on colour alone.
        render: (record: AssetMaintenanceRecord) =>
          record.verified ? (
            <Group gap={4} wrap='nowrap'>
              <IconCircleCheck size={16} />
              <Text size='sm'>{t`Verified`}</Text>
            </Group>
          ) : (
            <Text size='sm' c='dimmed'>
              {t`Not verified`}
            </Text>
          )
      },
      {
        accessor: 'follow_up_required',
        title: t`Follow-up`,
        sortable: false,
        defaultVisible: false,
        render: (record: AssetMaintenanceRecord) =>
          record.follow_up_required ? (
            <Group gap={4} wrap='nowrap'>
              <IconAlertTriangle size={16} />
              <Text size='sm'>{t`Follow-up required`}</Text>
            </Group>
          ) : (
            ''
          )
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
