/** Registry component rows for pump 18, as the Parts tab reads them. */
export function components(withStock: boolean) {
  const row = (pk: number, code: string, name: string, extra: object) => ({
    pk,
    uuid: `00000000-0000-4000-8000-${String(pk).padStart(12, '0')}`,
    machine: 18,
    machine_name: 'Synthetic pump',
    part: 500 + pk,
    part_name: name,
    virtual: false,
    code,
    name,
    status: 'draft',
    provenance: 'Inferred from source tags; physical assignment is unverified.',
    provenance_display: 'SCADA tag mapping',
    provenance_imported_at: null,
    review_note: '',
    reviewed_at: null,
    reference_position: null,
    open_work: { count: 0 },
    stock: withStock ? { quantity: 0 } : null,
    fitted: withStock
      ? { version: 0, current: null, history: [], pending: [] }
      : null,
    ...extra
  });
  return [
    row(1, 'PS-MOTOR-DE-BRG:1', 'Motor Drive-End Bearing', {
      reference_position: 'bearing',
      open_work: { count: 1 },
      stock: withStock ? { quantity: 6 } : null
    }),
    row(2, 'PS-MOTOR:1', 'Electric Motor', {
      reference_position: 'motor',
      status: 'verified',
      provenance: 'Workbook population 4c1f00a2e9b1',
      provenance_display: 'Equipment register import',
      provenance_imported_at: '2026-10-10T14:30:00Z',
      open_work: null
    }),
    row(3, 'PS-THRUST-BRG:1', 'Thrust Bearing', {})
  ];
}
