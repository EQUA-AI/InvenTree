import { describe, expect, it } from 'vitest';
import { componentSource } from './componentPresentation';

describe('component provenance presentation', () => {
  it('keeps raw import traceability without exposing a hash as its label', () => {
    expect(
      componentSource({ provenance: 'Workbook population f9006a91f8a9' })
    ).toEqual({
      label: 'Equipment register import',
      importedAt: null,
      raw: 'Workbook population f9006a91f8a9'
    });
  });
  it('uses only the supplied original import timestamp', () => {
    expect(
      componentSource({
        provenance: 'Workbook population f9006a91f8a9',
        provenance_display: 'Equipment register import',
        provenance_imported_at: '2026-10-10T09:00:00Z'
      }).importedAt
    ).toBe('2026-10-10T09:00:00Z');
    expect(
      componentSource({
        provenance: '',
        provenance_display: 'Equipment register import',
        provenance_imported_at: 'invalid'
      }).importedAt
    ).toBeNull();
  });
  it('does not add import dates to SCADA mappings or manual evidence', () => {
    expect(
      componentSource({
        provenance:
          'Inferred from source tags; physical assignment is unverified.',
        provenance_imported_at: '2026-10-10T09:00:00Z'
      })
    ).toMatchObject({ label: 'SCADA tag mapping', importedAt: null });
    expect(
      componentSource({ provenance: 'Operator register review' }).label
    ).toBe('Operator register review');
    expect(componentSource({ provenance: '' }).label).toBe('');
  });
});
