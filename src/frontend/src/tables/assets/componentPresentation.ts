import type { MachineComponent } from '@lib/types/Assets';
import { t } from '@lingui/core/macro';

export type ComponentSourceKind =
  | 'register'
  | 'scada'
  | 'tag_review'
  | 'manual'
  | 'other'
  | 'missing';
/** Prefer the typed API projection; recognize older servers without displaying raw provenance. */
export function componentSource(
  record: Pick<
    MachineComponent,
    | 'provenance'
    | 'provenance_display'
    | 'provenance_imported_at'
    | 'provenance_kind'
  >
) {
  const raw = record.provenance?.trim() ?? '';
  const source = record.provenance_display?.trim() || raw;
  const kind: ComponentSourceKind = record.provenance_kind
    ? record.provenance_kind === 'unknown'
      ? 'other'
      : record.provenance_kind
    : /^Workbook population [a-f0-9]{12}$/i.test(source) ||
        source === 'Equipment register import'
      ? 'register'
      : [
            'Inferred from source tags; physical assignment is unverified.',
            'SCADA tag mapping'
          ].includes(source)
        ? 'scada'
        : source === 'Explicit dictionary review crosswalk'
          ? 'tag_review'
          : [
                'Manual entry',
                'Operator review',
                'Operator register review'
              ].includes(source)
            ? 'manual'
            : source
              ? 'other'
              : 'missing';
  const imported = record.provenance_imported_at;
  return {
    kind,
    inferred: kind === 'scada',
    label: kind === 'manual' ? t`Manual entry` : '',
    importedAt:
      kind === 'register' && imported && Number.isFinite(Date.parse(imported))
        ? imported
        : null
  };
}
export type ComponentBasisRecord = Pick<
  MachineComponent,
  | 'status'
  | 'provenance'
  | 'provenance_display'
  | 'provenance_imported_at'
  | 'provenance_kind'
  | 'review_note'
>;
export function componentBasis(record: ComponentBasisRecord) {
  const source = componentSource(record);
  if (
    (source.kind === 'missing' || source.kind === 'other') &&
    record.review_note?.includes(
      'engineering=ENGINEERED_REFERENCE_PENDING_REVIEW'
    )
  )
    source.kind = 'register';
  if (record.status === 'verified')
    return { ...source, label: t`Verified`, reason: t`Assignment verified` };
  const labels = {
    register: t`Register import`,
    scada: t`Inferred from tags`,
    tag_review: t`Tag review`,
    manual: t`Pending check`,
    other: t`Pending check`,
    missing: t`Pending check`
  };
  const reasons = {
    register: t`From the equipment register's reference design; not field-verified. Variant and compatibility not approved`,
    scada: t`Matched from SCADA tag names; not checked on site`,
    tag_review: t`Mapped in a tag dictionary review; not checked on site`,
    manual: t`Assignment has not been verified`,
    other: t`Assignment basis is not confirmed`,
    missing: t`Assignment basis is not recorded`
  };
  return {
    ...source,
    label: labels[source.kind],
    reason: reasons[source.kind]
  };
}
export function componentReview(note: string): string {
  if (/engineering=|compatibility=/.test(note))
    return [
      /engineering=ENGINEERED_REFERENCE_PENDING_REVIEW/.test(note) &&
        t`Engineering review pending`,
      /compatibility=NOT_APPROVED/.test(note) && t`Compatibility not approved`
    ]
      .filter(Boolean)
      .join(' · ');
  return note.replace(/\b[0-9a-f]{12,64}\b/gi, '').trim();
}
