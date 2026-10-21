/**
 * R6 UI01: a machine's open Work as the Machines list shows it.
 *
 * The server counts open, active Work in the reader's canonical Work scope
 * and names the highest native priority. Its absence or a malformed value is
 * "not available" — never a zero that would read as "nothing to do".
 */

export type WorkPriority = 'low' | 'medium' | 'high';

export interface MachineOpenWork {
  count: number;
  highest_priority: WorkPriority | null;
}

export type MachineWorkSummary =
  | { kind: 'unavailable' }
  | { kind: 'none' }
  | { kind: 'open'; count: number; priority: WorkPriority | null };

const PRIORITIES: readonly WorkPriority[] = ['low', 'medium', 'high'];

export function machineWorkSummary(value: unknown): MachineWorkSummary {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    return { kind: 'unavailable' };
  const { count, highest_priority: priority } = value as Record<
    string,
    unknown
  >;
  if (typeof count !== 'number' || !Number.isSafeInteger(count) || count < 0)
    return { kind: 'unavailable' };
  const known = PRIORITIES.find((candidate) => candidate === priority) ?? null;
  if (priority !== null && known === null) return { kind: 'unavailable' };
  if (count === 0)
    return known === null ? { kind: 'none' } : { kind: 'unavailable' };
  return { kind: 'open', count, priority: known };
}
