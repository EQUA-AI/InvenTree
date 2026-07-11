import { Badge } from '@mantine/core';

import type { RepairPacketGate } from '@lib/types/Repair';

import { gateStatusColor, gateStatusLabel } from './safetyApi';

/** Compact status badge for a safety gate (never colour-only: always labelled). */
export function SafetyGateStatusBadge({
  gate
}: Readonly<{ gate: RepairPacketGate }>) {
  return (
    <Badge color={gateStatusColor(gate.status)} variant='light'>
      {gateStatusLabel(gate.status)}
    </Badge>
  );
}
