import { t } from '@lingui/core/macro';

import type { TwinMode, TwinPartId } from './twinScene.mjs';

/**
 * The reference model's positions, without loading the 3D bundle: the
 * component list and the panel need them before (and if) the scene starts.
 * They mirror the scene's own PARTS (a unit test keeps them in step);
 * records are bound to these IDs, never to meshes or their order.
 */
export const REFERENCE_PARTS: readonly {
  id: TwinPartId;
  number: number;
  parent: TwinPartId | null;
}[] = [
  { id: 'machine', number: 0, parent: null },
  { id: 'motor', number: 1, parent: 'machine' },
  { id: 'bearing', number: 2, parent: 'motor' },
  { id: 'coupling', number: 3, parent: 'machine' },
  { id: 'seal', number: 4, parent: 'pump' },
  { id: 'impeller', number: 5, parent: 'pump' },
  { id: 'pump', number: 6, parent: 'machine' },
  { id: 'discharge', number: 7, parent: 'pump' },
  { id: 'base', number: 8, parent: 'machine' }
];

/** How far a position sits below the complete machine. */
export function referenceDepth(id: TwinPartId): number {
  let depth = 0;
  let parent = REFERENCE_PARTS.find((part) => part.id === id)?.parent ?? null;
  while (parent && parent !== 'machine') {
    depth += 1;
    parent = REFERENCE_PARTS.find((part) => part.id === parent)?.parent ?? null;
  }
  return id === 'machine' ? 0 : depth + 1;
}

/**
 * The list order: each assembly followed by what it holds, so the drive
 * train reads top to bottom (motor, its bearing, coupling, pump and its
 * internals, base) rather than in the scene's numbering.
 */
export function referenceListOrder(): TwinPartId[] {
  return [...REFERENCE_PARTS]
    .sort((a, b) => a.number - b.number)
    .map((part) => part.id);
}

export function referencePartLabel(id: TwinPartId): string {
  switch (id) {
    case 'machine':
      return t`Complete pump train`;
    case 'motor':
      return t`Electric motor`;
    case 'bearing':
      return t`Motor drive-end bearing`;
    case 'coupling':
      return t`Flexible coupling`;
    case 'seal':
      return t`Mechanical seal`;
    case 'impeller':
      return t`Impeller`;
    case 'pump':
      return t`Pump casing and suction`;
    case 'discharge':
      return t`Discharge and valve`;
    case 'base':
      return t`Base and anchors`;
  }
}

export function referenceModeLabel(mode: TwinMode): string {
  switch (mode) {
    case 'assembled':
      return t`Assembled`;
    case 'cutaway':
      return t`Cutaway`;
    case 'exploded':
      return t`Exploded`;
  }
}
