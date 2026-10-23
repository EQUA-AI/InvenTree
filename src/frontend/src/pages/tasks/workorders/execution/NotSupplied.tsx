import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';
import { t } from '@lingui/core/macro';
import { ActionIcon, Box, Stack, Text, Tooltip } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { factTooltipProps } from '../../../../components/items/ValueTooltip';
import type { WorkPanel } from '../workPanelState';

/** Schema/provider gaps, distinct from empty records and omitted human entries. */
export function NotSupplied({
  work,
  panel
}: Readonly<{ work: WorkOrderOverview; panel: WorkPanel }>) {
  const gaps: string[] = [];
  if (['work', 'verify', 'closeout'].includes(panel)) {
    gaps.push(
      t`Condition-control authority and a complete residual-obligations register are not supplied. Their applicability, accepted owners and deadlines are unknown.`
    );
    if (!work.operational_authority)
      gaps.push(
        t`Test acceptance, operating permission, observed return, required service and standby capacity: authoritative records are not supplied by this server.`
      );
  }
  if (panel === 'work') {
    if (!work.handled_actions)
      gaps.push(
        t`This server does not supply a record of AIMMS digital work for this work order. Nothing here means AIMMS did or did not act.`
      );
    if (work.work_continuity?.rule !== 'work_continuity.v1')
      gaps.push(
        t`Work continuity not supplied. This server does not supply hold provenance or site handover acceptance here. Assignment and opening a panel do not establish accepted responsibility or physical permission.`
      );
    gaps.push(
      t`Equipment physical state, service / standby availability, current hazards / prohibited actions and operating window / access authority are not supplied as one current handover snapshot. Review individual recorded authority sources in Plan & Safety and Verify & Return.`
    );
    if (!work.time_assignment?.assignment_event_at)
      gaps.push(
        t`Assignment-event attribution and elapsed assignment are not supplied. Native job timestamps are not person-work intervals.`
      );
    if (!work.time_assignment?.assignment_factors?.available)
      gaps.push(
        t`Assignment factors are not supplied; no assignment score is inferred.`
      );
    const readings = work.routine_work?.readings ?? [];
    if (
      readings.some(
        (r) => r.verification_state === 'dispositioned' || r.disposition_reason
      )
    )
      gaps.push(
        t`Disposition recorded by: Not recorded. Disposition recorded at: Not recorded. These fields are not retained in this reading record.`
      );
    // Only a dispositioned reading has replaced an earlier evaluation.
    if (readings.some((r) => r.verification_state === 'dispositioned'))
      gaps.push(
        t`The earlier verification outcome of a dispositioned reading is not retained in this reading record.`
      );
    if (
      work.work_order_type === 'calibration' &&
      work.routine_work?.readings.some(
        (row) => row.calibration_traceability === undefined
      )
    )
      gaps.push(
        t`Calibration traceability unavailable — this server does not supply the calibration record projection for every returned reading. Instrument, certificate, actor and time remain unknown for those readings.`
      );
    if (work.routine_work?.readings.length)
      gaps.push(
        t`Reading limit provenance and explicit before/after pairing identifiers are not supplied. Phase labels and step links alone do not establish a matched pair.`
      );
  }
  if (panel === 'diagnose' && !work.investigation)
    gaps.push(
      t`Saved investigation explanation, request queue and cited evidence are not supplied in this overview.`
    );
  if (
    ['plan', 'parts'].includes(panel) &&
    !work.prepared_package?.revision_review?.selective_reassessment
  )
    gaps.push(
      t`Scope-to-task, material, resource, permission and test dependency impact is not supplied. Earlier approval does not automatically cover changed work.`
    );
  if (panel === 'plan' && !work.operational_authority)
    gaps.push(
      t`Physical work authority is not supplied. Saved gates and software permissions do not authorize physical work.`
    );
  if (panel === 'verify')
    gaps.push(
      t`Conditions and instruments for procedure test results are not supplied. Earlier values remain unavailable unless explicitly retained.`,
      t`Controlled-test authority, its permitted scope, conditions, responsible owner and effective time are not supplied by this projection.`
    );
  if (panel === 'history')
    gaps.push(
      t`The visible activity and linked records are not a complete register of service, financial, quality or regulatory obligations.`
    );
  if (!gaps.length) return null;
  return (
    <Box data-testid='not-supplied'>
      <Tooltip
        {...factTooltipProps}
        withinPortal={false}
        label={
          <Stack gap='xs' mah='65dvh' style={{ overflowY: 'auto' }}>
            {gaps.map((gap) => (
              <Text key={gap} size='xs'>
                {gap}
              </Text>
            ))}
          </Stack>
        }
      >
        <ActionIcon
          variant='subtle'
          color='gray'
          aria-label={t`Record coverage`}
        >
          <IconInfoCircle size={16} aria-hidden />
        </ActionIcon>
      </Tooltip>
    </Box>
  );
}
