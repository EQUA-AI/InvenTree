/** A locator is useful only after it has been checked against the opened revision. */
export function validVideoSegment(
  start: unknown,
  end: unknown,
  duration: number
): boolean {
  return (
    typeof start === 'number' &&
    Number.isFinite(start) &&
    start >= 0 &&
    Number.isFinite(duration) &&
    duration > 0 &&
    start < duration &&
    (end == null ||
      (typeof end === 'number' &&
        Number.isFinite(end) &&
        end > start &&
        end <= duration))
  );
}

export function validDocumentPage(
  index: unknown,
  count: unknown
): index is number {
  return (
    typeof index === 'number' &&
    Number.isSafeInteger(index) &&
    index >= 0 &&
    typeof count === 'number' &&
    Number.isSafeInteger(count) &&
    count > index
  );
}
import type { MediaEvidenceItem } from '../../hooks/UseAIChat';

export type SourceMetadata = {
  revision: string;
  content_type: string;
  page_count: number | null;
  page_labels: string[];
};

/** Validate the network boundary before using metadata as locator authority. */
export function parseSourceMetadata(
  value: unknown,
  revision?: string | null
): SourceMetadata {
  if (!value || typeof value !== 'object')
    throw new Error('Source unavailable');
  const data = value as Record<string, unknown>;
  if (
    typeof data.revision !== 'string' ||
    !/^[a-f0-9]{64}$/.test(data.revision) ||
    (revision && data.revision !== revision) ||
    typeof data.content_type !== 'string' ||
    ![
      'application/pdf',
      'image/png',
      'image/jpeg',
      'image/webp',
      'image/gif',
      'video/mp4',
      'video/quicktime'
    ].includes(data.content_type) ||
    (data.page_count !== null &&
      (typeof data.page_count !== 'number' ||
        !Number.isSafeInteger(data.page_count) ||
        data.page_count <= 0)) ||
    !Array.isArray(data.page_labels) ||
    data.page_labels.length > 10000 ||
    !data.page_labels.every(
      (label) => typeof label === 'string' && label.length <= 100
    )
  ) {
    throw new Error('Source unavailable');
  }
  return data as SourceMetadata;
}

/** Source identity and locator both participate in chip and viewer identity. */
export function evidenceKey(item: MediaEvidenceItem): string {
  return JSON.stringify([
    item.attachment_id,
    item.source_revision ?? null,
    item.media_type,
    item.page_index ?? null,
    item.segment_index,
    item.timecode_start_s ?? null,
    item.timecode_end_s ?? null
  ]);
}
