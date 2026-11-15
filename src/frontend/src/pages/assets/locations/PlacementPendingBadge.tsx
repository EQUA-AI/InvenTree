import { t } from '@lingui/core/macro';
import { Badge, Tooltip } from '@mantine/core';

import { type PlacementRow, placementPending } from './placementPending';

/** Projection bookkeeping only: never a physical move, alarm or global sync claim. */
export function PlacementPendingBadge({
  machine,
  checking = false
}: Readonly<{ machine: PlacementRow; checking?: boolean }>) {
  const state = placementPending(machine);
  if (state === null) return null;
  const label = checking
    ? t`Checking placement sync`
    : state === 'pending'
      ? t`Placement projection pending`
      : state === 'current'
        ? t`Placement projection current`
        : t`Placement sync unverified`;
  const detail = checking
    ? t`Checking the latest authorized placement metadata. The last loaded status is not a new verification.`
    : state === 'pending'
      ? t`The AIMMS placement is saved. Its latest version has not yet been verified in the enrolled projection. Native operations remain available.`
      : state === 'current'
        ? t`The latest AIMMS placement was verified in the enrolled projection for this read. This does not verify other machine data or physical movement.`
        : t`Placement synchronization could not be verified. This is not evidence that the projection is current or that a native move failed.`;
  return (
    <Tooltip
      label={detail}
      multiline
      maw={360}
      events={{ hover: true, focus: true, touch: true }}
    >
      <Badge
        size='sm'
        variant='outline'
        color={!checking && state === 'current' ? 'blue' : 'gray'}
        c='var(--mantine-color-text)'
        tt='none'
        tabIndex={0}
        data-placement-sync={checking ? 'checking' : state}
        styles={{
          root: { height: 'auto', maxWidth: '100%' },
          label: { whiteSpace: 'normal', paddingBlock: 3 }
        }}
      >
        {label}
      </Badge>
    </Tooltip>
  );
}
