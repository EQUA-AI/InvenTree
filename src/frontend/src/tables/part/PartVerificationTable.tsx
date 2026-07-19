import { t } from '@lingui/core/macro';
import { useMemo } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import useTable from '@lib/hooks/UseTable';
import type { TableFilter } from '@lib/types/Filters';
import type { TableColumn } from '@lib/types/Tables';
import { InvenTreeTable } from '../../components/tables/InvenTreeTable';

/**
 * Read shape of a part verification session row.
 */
export interface PartVerificationSessionRow {
  pk: number;
  reference: string;
  purpose: string;
  state: string;
  revision: number;
  requested_part_name: string | null;
  eligible_count: number;
  considered_count: number;
  universe_complete: boolean;
  stale_reason: string;
  expires_at: string | null;
  updated_at: string;
}

/**
 * Translated text label for a session state value.
 */
export function verificationStateLabel(state: string): string {
  switch (state) {
    case 'collecting':
      return t`Collecting`;
    case 'evaluating':
      return t`Evaluating`;
    case 'review_required':
      return t`Review Required`;
    case 'confirmed':
      return t`Confirmed`;
    case 'no_safe_match':
      return t`No Safe Match`;
    case 'stale':
      return t`Stale`;
    case 'cancelled':
      return t`Cancelled`;
    default:
      return state;
  }
}

/**
 * Translated text label for a session purpose value.
 */
export function verificationPurposeLabel(purpose: string): string {
  switch (purpose) {
    case 'installed_replacement':
      return t`Installed Replacement`;
    case 'bom_component':
      return t`BOM Component`;
    case 'job_kit_substitution':
      return t`Job Kit Substitution`;
    case 'rfq_demand':
      return t`RFQ Demand`;
    case 'po_line':
      return t`Purchase Order Line`;
    case 'manual':
      return t`Manual Verification`;
    default:
      return purpose;
  }
}

/**
 * Table component for displaying Right-Part Finder verification sessions.
 */
export function PartVerificationTable() {
  const table = useTable('part-verification');

  const tableColumns: TableColumn<PartVerificationSessionRow>[] =
    useMemo(() => {
      return [
        {
          accessor: 'reference',
          title: t`Reference`,
          sortable: false
        },
        {
          accessor: 'purpose',
          title: t`Purpose`,
          sortable: false,
          render: (record: PartVerificationSessionRow) =>
            verificationPurposeLabel(record.purpose)
        },
        {
          accessor: 'state',
          title: t`State`,
          sortable: false,
          render: (record: PartVerificationSessionRow) =>
            verificationStateLabel(record.state)
        },
        {
          accessor: 'revision',
          title: t`Revision`,
          sortable: false
        },
        {
          accessor: 'requested_part_name',
          title: t`Requested Part`,
          sortable: false
        },
        {
          accessor: 'eligible_count',
          title: t`Eligible`,
          sortable: false
        },
        {
          accessor: 'considered_count',
          title: t`Considered`,
          sortable: false
        },
        {
          accessor: 'universe_complete',
          title: t`Universe Complete`,
          sortable: false,
          render: (record: PartVerificationSessionRow) =>
            record.universe_complete ? t`Yes` : t`No`
        },
        {
          accessor: 'stale_reason',
          title: t`Stale Reason`,
          sortable: false
        },
        {
          accessor: 'expires_at',
          title: t`Expires`,
          sortable: false
        },
        {
          accessor: 'updated_at',
          title: t`Updated`,
          sortable: false
        }
      ];
    }, []);

  const tableFilters: TableFilter[] = useMemo(() => {
    return [
      {
        name: 'state',
        label: t`State`,
        description: t`Filter by session state`,
        type: 'choice',
        choices: [
          { value: 'collecting', label: t`Collecting` },
          { value: 'evaluating', label: t`Evaluating` },
          { value: 'review_required', label: t`Review Required` },
          { value: 'confirmed', label: t`Confirmed` },
          { value: 'no_safe_match', label: t`No Safe Match` },
          { value: 'stale', label: t`Stale` },
          { value: 'cancelled', label: t`Cancelled` }
        ]
      },
      {
        name: 'purpose',
        label: t`Purpose`,
        description: t`Filter by session purpose`,
        type: 'choice',
        choices: [
          { value: 'installed_replacement', label: t`Installed Replacement` },
          { value: 'bom_component', label: t`BOM Component` },
          { value: 'job_kit_substitution', label: t`Job Kit Substitution` },
          { value: 'rfq_demand', label: t`RFQ Demand` },
          { value: 'po_line', label: t`Purchase Order Line` },
          { value: 'manual', label: t`Manual Verification` }
        ]
      }
    ];
  }, []);

  return (
    <InvenTreeTable<PartVerificationSessionRow>
      url={apiUrl(ApiEndpoints.part_verification_session_list)}
      tableState={table}
      columns={tableColumns}
      props={{
        modelType: ModelType.partverificationsession,
        enableSearch: false,
        enablePagination: true,
        enableRefresh: true,
        enableColumnSwitching: true,
        tableFilters: tableFilters
      }}
    />
  );
}
