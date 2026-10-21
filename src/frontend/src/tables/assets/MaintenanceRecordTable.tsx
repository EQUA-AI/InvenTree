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
 *
 * `boundToMachine` (R6 P12, the Maintenance tab's Completed history view):
 * the table belongs to this machine, so the page URL's own parameters - the
 * Machines list's return state, the tab's view - are not read as filters on
 * it. Its own search, sorting and pagination are unchanged.
 */
export function MaintenanceRecordTable({
  machineId,
  boundToMachine = false
}: Readonly<{ machineId: number; boundToMachine?: boolean }>) {
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
                {t`No work order you can open`}
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
        // R6 P12: the component the linked work order recorded.
        accessor: 'work_order_component',
        title: t`Component`,
        sortable: false,
        render: (record: AssetMaintenanceRecord) =>
          record.work_order_component ? (
            record.work_order_component
          ) : (
            <Text size='sm' c='dimmed'>
              {record.work_order ? t`Not recorded` : t`Not available`}
            </Text>
          )
      },
      {
        accessor: 'work_order_type',
        title: t`Type`,
        sortable: false,
        // The badge reads whole beside the added Component and Follow-up
        // columns (R6 P12) instead of truncating to "CORRE…".
        minWidth: 120,
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
          record.amended ? (
            <Group gap={4} wrap='nowrap'>
              <Text size='sm'>{formatDowntime(record.downtime_minutes)}</Text>
              <Badge size='xs' color='yellow' variant='light'>
                {t`Amended`}
              </Badge>
            </Group>
          ) : (
            formatDowntime(record.downtime_minutes)
          )
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
        // R6 UI06: residual conditions are shown where present.
        defaultVisible: true,
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
        ...(boundToMachine ? { enableQueryFilters: false } : {}),
        params: {
          machine: machineId
        }
      }}
    />
  );
}
