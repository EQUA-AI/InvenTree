import type { AssistantPresentationV1 } from '@lib/types/AimmsWire.generated';

const kinds = new Set([
  'entity',
  'evidence',
  'metric_dataset',
  'trend_dataset',
  'chart_dataset'
]);
const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);
/** One total boundary for live, replay and history. No parsing/evaluation here. */
export function normalizePresentation(
  raw: unknown
): AssistantPresentationV1 | null {
  try {
    if (
      !record(raw) ||
      Object.keys(raw).sort().join(',') !==
        'format,library,program,references,schema_version' ||
      raw.schema_version !== 1 ||
      raw.format !== 'openui-lang' ||
      !['aimms-chat@1', 'aimms-chat@2'].includes(String(raw.library)) ||
      typeof raw.program !== 'string' ||
      !raw.program.trim() ||
      new TextEncoder().encode(raw.program).length > 65536 ||
      !Array.isArray(raw.references) ||
      raw.references.length > 100
    )
      return null;
    const seen = new Set<string>();
    for (const ref of raw.references) {
      if (
        !record(ref) ||
        Object.keys(ref).sort().join(',') !== 'kind,ref_id,source_key' ||
        typeof ref.ref_id !== 'string' ||
        !ref.ref_id.trim() ||
        ref.ref_id.length > 128 ||
        seen.has(ref.ref_id) ||
        typeof ref.kind !== 'string' ||
        !kinds.has(ref.kind) ||
        (raw.library === 'aimms-chat@1' && ref.kind === 'chart_dataset') ||
        typeof ref.source_key !== 'string' ||
        !ref.source_key.trim() ||
        ref.source_key.length > 256
      )
        return null;
      seen.add(ref.ref_id);
    }
    // Detach from mutable transport input.
    return {
      ...raw,
      references: raw.references.map((ref) => ({ ...ref }))
    } as AssistantPresentationV1;
  } catch {
    return null;
  }
}
