import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { Anchor, Stack, Text, Title } from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { RecordedDowntime } from './RecordedDowntime';
import { WorkMaterialsPanel } from './WorkMaterialsPanel';
import { TimeAssignment } from './execution/TimeAssignment';

/** Read the native material resource only on disclosure; share its Parts cache. */
export function CloseoutReconciliation({
  workOrder
}: Readonly<{ workOrder: WorkOrderOverview }>) {
  const [readMaterials, setReadMaterials] = useState(false);
  return (
    <Stack gap='sm' data-testid='closeout-reconciliation'>
      <Stack gap='sm'>
        <Title
          order={3}
          size='h5'
        >{t`Material custody, time and downtime`}</Title>
        <Text size='sm'>{t`Usage and custody are separate records. A reservation or a reconciled usage row does not prove fitted identity or stock release.`}</Text>
        <details
          onToggle={(event) => {
            if (event.currentTarget.open) setReadMaterials(true);
          }}
        >
          <summary>{t`Read material custody and serial / lot records`}</summary>
          {readMaterials && (
            <WorkMaterialsPanel
              workOrderId={workOrder.id}
              label={workOrder.reference || t`Work ${workOrder.id}`}
              returnLabel={t`${workOrder.reference || workOrder.id} · Closeout`}
              fallback={
                <Text size='sm'>{t`Detailed custody is unavailable here. Open Parts for the native requirement records.`}</Text>
              }
            />
          )}
        </details>
        <Text size='sm'>{t`Removed-item identity, retained-for-analysis, repair/refurbishment, quarantine release and warranty/core context are not supplied by this readout. Keep an unresolved item in its true custody; no material movement is performed here.`}</Text>
        <Text
          size='xs'
          c='dimmed'
        >{t`Discrepancy owners, business-record ownership and next checkpoints are unavailable where the source does not name them. A recorded resolver is not an assigned owner.`}</Text>
        <Anchor
          component={Link}
          to='?work_panel=parts'
        >{t`Open Parts and native inventory records`}</Anchor>
        <Text size='sm' fw={600}>
          {workOrder.time_assignment?.time_records?.intervals.length
            ? t`Recorded person-work intervals available · coverage partial`
            : t`Attributed time: unavailable · coverage unknown`}
        </Text>
        <details>
          <summary>{t`Read assignment provenance and native time`}</summary>
          <TimeAssignment workOrder={workOrder} />
        </details>
        <RecordedDowntime key={workOrder.id} workOrder={workOrder} />
      </Stack>
    </Stack>
  );
}
