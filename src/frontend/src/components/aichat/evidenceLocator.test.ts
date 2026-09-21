import { describe, expect, it } from 'vitest';
import type { MediaEvidenceItem } from '../../hooks/UseAIChat';
import {
  evidenceKey,
  parseSourceMetadata,
  validDocumentPage,
  validVideoSegment
} from './evidenceLocator';

describe('revision metadata locators', () => {
  it('rejects malformed metadata and silent revision substitution', () => {
    const metadata = {
      revision: 'a'.repeat(64),
      content_type: 'application/pdf',
      page_count: 2,
      page_labels: ['i', '1']
    };
    expect(parseSourceMetadata(metadata, metadata.revision)).toEqual(metadata);
    for (const invalid of [
      null,
      {},
      { ...metadata, page_count: -1 },
      { ...metadata, page_count: 1.5 },
      { ...metadata, page_labels: '12' },
      { ...metadata, content_type: 'text/html' }
    ]) {
      expect(() => parseSourceMetadata(invalid)).toThrow('Source unavailable');
    }
    expect(() => parseSourceMetadata(metadata, 'b'.repeat(64))).toThrow(
      'Source unavailable'
    );
  });
  it('gives different document pages and revisions separate viewer identities', () => {
    const item: MediaEvidenceItem = {
      attachment_id: 9,
      model_type: 'part',
      media_type: 'document',
      segment_index: 0,
      page_index: 0,
      source_revision: 'a'.repeat(64),
      label: 'Document'
    };
    expect(evidenceKey(item)).toBe(evidenceKey({ ...item }));
    expect(evidenceKey(item)).not.toBe(evidenceKey({ ...item, page_index: 1 }));
    expect(evidenceKey(item)).not.toBe(
      evidenceKey({ ...item, source_revision: 'b'.repeat(64) })
    );
  });
  it('checks physical first and last pages without clamping invalid input', () => {
    expect(validDocumentPage(0, 2)).toBe(true);
    expect(validDocumentPage(1, 2)).toBe(true);
    for (const page of [-1, 2, 1.5, '1', null, Number.NaN])
      expect(validDocumentPage(page, 2)).toBe(false);
    expect(validDocumentPage(0, null)).toBe(false);
  });
  it('requires finite video metadata and valid ranges, including zero', () => {
    expect(validVideoSegment(0, 2, 10)).toBe(true);
    expect(validVideoSegment(5, null, 10)).toBe(true);
    for (const start of [
      -1,
      10,
      Number.POSITIVE_INFINITY,
      Number.NaN,
      null,
      '0'
    ])
      expect(validVideoSegment(start, null, 10)).toBe(false);
    expect(validVideoSegment(0, null, Number.POSITIVE_INFINITY)).toBe(false);
    expect(validVideoSegment(2, 2, 10)).toBe(false);
    expect(validVideoSegment(2, 11, 10)).toBe(false);
  });
});
