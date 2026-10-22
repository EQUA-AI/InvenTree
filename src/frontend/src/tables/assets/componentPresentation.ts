import type { MachineComponent } from '@lib/types/Assets';

export type ComponentSourceKind =
  | 'register'
  | 'scada'
  | 'manual'
  | 'other'
  | 'missing';

/** Normalize the legacy provider vocabulary once, before translation/rendering. */
export function componentSource(
  record: Pick<
    MachineComponent,
    'provenance' | 'provenance_display' | 'provenance_imported_at'
  >
) {
  const raw = record.provenance?.trim() ?? '';
  const source = record.provenance_display?.trim() || raw;
  const kind: ComponentSourceKind = !source
    ? 'missing'
    : /^Workbook population [a-f0-9]{12}$/i.test(source) ||
        source === 'Equipment register import'
      ? 'register'
      : source ===
            'Inferred from source tags; physical assignment is unverified.' ||
          source === 'SCADA tag mapping'
        ? 'scada'
        : [
              'Manual entry',
              'Operator review',
              'Operator register review'
            ].includes(source)
          ? 'manual'
          : 'other';
  const imported = record.provenance_imported_at;
  return {
    kind,
    label: kind === 'register' || kind === 'scada' ? '' : source,
    importedAt:
      kind === 'register' && imported && Number.isFinite(Date.parse(imported))
        ? imported
        : null,
    raw
  };
}
