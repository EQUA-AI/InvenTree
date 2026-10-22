import { t } from '@lingui/core/macro';
import { Anchor, Badge, Group, Text, VisuallyHidden } from '@mantine/core';
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconInfoCircle
} from '@tabler/icons-react';
import { useEffect, useMemo, useRef } from 'react';
import { Link } from 'react-router-dom';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { AssetMaintenanceRecord } from '@lib/types/Assets';
import type { TableColumn } from '@lib/types/Tables';
import { RefreshAction } from '../../components/buttons/RefreshAction';
import {
  MissingValue,
  ValueTooltip
} from '../../components/items/ValueTooltip';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';
import { Timestamp } from '../../pages/assets/health/Timestamp';
import {
  emptyMaintenanceColumns,
  maintenanceMissingReasons
} from '../../pages/assets/maintenance/maintenancePresentation';

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
  boundToMachine = false,
  onRefresh
}: Readonly<{
  machineId: number;
  boundToMachine?: boolean;
  onRefresh?: () => void;
}>) {
  const table = useTable('maintenance-record');
  const records = table.records as AssetMaintenanceRecord[];
  const emptyColumns = useMemo(
    () => emptyMaintenanceColumns(records),
    [records]
  );
  const pageSignature = JSON.stringify([
    records.map((record) => record.pk),
    emptyColumns
  ]);
  const previousPage = useRef<string>('');
  const automaticallyHidden = useRef<string[]>([]);
  // Apply the compact default once per loaded page. A user can still reveal a
  // column from the native chooser; an unrelated render never hides it again.
  useEffect(() => {
    if (previousPage.current === pageSignature) return;
    previousPage.current = pageSignature;
    const next = Object.keys(emptyColumns);
    const manual = table.hiddenColumns.filter(
      (column) => !automaticallyHidden.current.includes(column)
    );
    automaticallyHidden.current = next;
    table.setHiddenColumns([...new Set([...manual, ...next])]);
  }, [
    pageSignature,
    emptyColumns,
    table.hiddenColumns,
    table.setHiddenColumns
  ]);

  const tableColumns: TableColumn<AssetMaintenanceRecord>[] = useMemo(() => {
    return [
      {
        accessor: 'record_information',
        title: <VisuallyHidden>{t`Record information`}</VisuallyHidden>,
        switchable: false,
        width: 40,
        minWidth: 40,
        render: (record) => {
          const reasons = Object.values(maintenanceMissingReasons(record));
          return reasons.length ? (
            <ValueTooltip
              label={reasons.join(' · ')}
              accessibleLabel={t`Record information`}
            >
              <IconInfoCircle size={16} />
            </ValueTooltip>
          ) : null;
        }
      },
      {
        accessor: 'date',
        title: t`Date`,
        sortable: true,
        noWrap: true,
        render: (record) => <Timestamp value={record.date} dateOnly />
      },
      {
        accessor: 'summary',
        title: t`Summary`,
        sortable: true,
        render: (record) =>
          record.summary || (
            <MissingValue focusable={false} reason={t`Summary not recorded`} />
          )
      },
      {
        accessor: 'work_order',
        title: t`Work Order`,
        sortable: false,
        render: (record: AssetMaintenanceRecord) => {
          if (!record.work_order) {
            return (
              <MissingValue
                focusable={false}
                reason={
                  record.work_order_link_state === 'none'
                    ? t`No linked work order`
                    : t`Work order link not available`
                }
              />
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
        defaultVisible: false,
        render: (record) =>
          record.work_order_title || (
            <MissingValue focusable={false} reason={t`Job not recorded`} />
          )
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
            <MissingValue
              focusable={false}
              reason={t`Component not recorded`}
            />
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
            return (
              <MissingValue
                focusable={false}
                reason={t`Work type not recorded`}
              />
            );
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
        sortable: false,
        render: (record) =>
          record.performed_by || (
            <MissingValue
              focusable={false}
              reason={t`Technician not recorded`}
            />
          )
      },
      {
        accessor: 'lifecycle_status',
        title: t`Outcome`,
        sortable: false,
        render: (record: AssetMaintenanceRecord) =>
          record.lifecycle_status === 'completed' ? (
            t`Completed`
          ) : record.lifecycle_status ? (
            <ValueTooltip
              label={t`Outcome classification not recognized`}
            >{t`Unknown`}</ValueTooltip>
          ) : (
            <MissingValue focusable={false} reason={t`Outcome not recorded`} />
          )
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
          ) : record.downtime_minutes == null ? (
            <MissingValue focusable={false} reason={t`Downtime not recorded`} />
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
            <Badge
              variant='outline'
              color='gray'
              c='var(--mantine-color-text)'
              tt='none'
              styles={{ label: { whiteSpace: 'nowrap' } }}
            >
              {t`Not verified`}
            </Badge>
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
          ) : record.follow_up_required === false ? (
            t`No`
          ) : (
            <MissingValue
              focusable={false}
              reason={t`Follow-up status not recorded`}
            />
          )
      },
      {
        accessor: 'details',
        title: t`Details`,
        sortable: false,
        defaultVisible: false,
        render: (record) =>
          record.details || (
            <MissingValue focusable={false} reason={t`Details not recorded`} />
          )
      }
    ];
  }, []);

  return (
    <InvenTreeTable<AssetMaintenanceRecord>
      url={apiUrl(ApiEndpoints.asset_maintenance_list)}
      tableState={table}
      columns={tableColumns.map((column) => ({
        ...column,
        title: emptyColumns[column.accessor] ? (
          <ValueTooltip focusable={false} label={emptyColumns[column.accessor]}>
            {column.title}
          </ValueTooltip>
        ) : (
          column.title
        )
      }))}
      props={{
        enableSearch: true,
        enablePagination: true,
        enableRefresh: true,
        refreshControl: (
          <RefreshAction
            onClick={() => {
              table.refreshTable();
              onRefresh?.();
            }}
          />
        ),
        hideSinglePagePagination: true,
        ...(boundToMachine ? { enableQueryFilters: false } : {}),
        params: {
          machine: machineId
        }
      }}
    />
  );
}
