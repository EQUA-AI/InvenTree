import { describe, expect, it, vi } from 'vitest';
vi.mock('@lingui/core/macro', () => ({
  t: (parts: TemplateStringsArray, ...args: unknown[]) =>
    parts.reduce((text, part, i) => text + part + (args[i] ?? ''), '')
}));
import {
  componentBasis,
  componentReview,
  componentSource
} from './componentPresentation';
describe('component assignment basis', () => {
  it.each([
    ['register', 'Reference design'],
    ['scada', 'Inferred from tags'],
    ['tag_review', 'Tag review'],
    ['unknown', 'Pending check'],
    ['manual', 'Pending check']
  ] as const)(
    'uses %s provenance without claiming verification',
    (kind, label) => {
      const record = {
        status: 'draft' as const,
        provenance: '',
        review_note: '',
        provenance_kind: kind
      };
      expect(componentBasis(record).label).toBe(label);
      expect(componentBasis({ ...record, status: 'verified' }).label).toBe(
        'Verified'
      );
    }
  );
  it('prefers the typed API kind and safe display projection over raw hashes', () => {
    const record = {
      provenance: 'Workbook population f9006a91f8a9',
      provenance_display: 'Equipment register import',
      provenance_imported_at: '2026-10-10T09:00:00Z'
    };
    expect(componentSource(record)).toMatchObject({
      kind: 'register',
      importedAt: record.provenance_imported_at
    });
    expect(JSON.stringify(componentSource(record))).not.toContain(
      'f9006a91f8a9'
    );
    expect(
      componentSource({ ...record, provenance_kind: 'unknown' }).kind
    ).toBe('other');
    expect(
      componentSource({ ...record, provenance_imported_at: 'invalid' })
        .importedAt
    ).toBeNull();
    expect(
      componentSource({ ...record, provenance_kind: 'scada' }).importedAt
    ).toBeNull();
  });
  it('supports old API vocabulary without exposing raw source notes', () => {
    expect(
      componentSource({
        provenance:
          'Inferred from source tags; physical assignment is unverified.'
      })
    ).toMatchObject({ kind: 'scada', inferred: true });
    expect(
      componentSource({ provenance: 'Explicit dictionary review crosswalk' })
        .kind
    ).toBe('tag_review');
    expect(
      componentSource({ provenance: 'Unclassified source note' }).label
    ).toBe('');
  });
  it('translates engineering tokens without leaking their raw keys or hashes', () => {
    expect(
      componentReview(
        'engineering=ENGINEERED_REFERENCE_PENDING_REVIEW compatibility=NOT_APPROVED'
      )
    ).toBe('Engineering review pending · Compatibility not approved');
    expect(componentReview('Review f9006a91f8a9')).toBe('Review');
  });
});
