import type { MachineComponent } from '@lib/types/Assets';

/** Older APIs keep their raw source; never turn a hash into visible copy. */
export function componentSource(
  record: Pick<
    MachineComponent,
    'provenance' | 'provenance_display' | 'provenance_imported_at'
  >
) {
  const raw = record.provenance?.trim() ?? '';
  const source = record.provenance_display?.trim() || raw;
  const label = /^Workbook population [a-f0-9]{12}$/i.test(source)
    ? 'Equipment register import'
    : source === 'Inferred from source tags; physical assignment is unverified.'
      ? 'SCADA tag mapping'
      : source;
  const imported = record.provenance_imported_at;
  return {
    label,
    importedAt:
      label === 'Equipment register import' &&
      imported &&
      Number.isFinite(Date.parse(imported))
        ? imported
        : null,
    raw
  };
}
