import { ModelType } from '@lib/enums/ModelType';
import { describe, expect, it } from 'vitest';
import {
  type AttachmentPreviewRecord,
  attachmentPreviewItem
} from './attachmentPreview';

const owner = { model_type: ModelType.assetmachine, model_id: 18 };
const record: AttachmentPreviewRecord = {
  pk: 901,
  model_type: 'assetmachine',
  model_id: 18,
  attachment: '/media/attachments/assetmachine/18/nameplate.png',
  filename: 'nameplate.png',
  link: null
};

describe('native machine and work attachment preview projection', () => {
  it.each([
    ['manual.PDF', 'document'],
    ['nameplate.png', 'image'],
    ['nameplate.jpeg', 'image'],
    ['nameplate.webp', 'image'],
    ['inspection.mov', 'video'],
    ['inspection.mp4', 'video']
  ])('uses exact owner and source identity for %s', (filename, mediaType) => {
    expect(attachmentPreviewItem({ ...record, filename }, owner)).toEqual({
      attachment_id: 901,
      model_type: 'assetmachine',
      model_id: 18,
      media_type: mediaType,
      label: filename
    });
  });

  it('does not invent revision, page, timecode or segment identity', () => {
    const item = attachmentPreviewItem(record, owner);
    expect(item).not.toHaveProperty('source_revision');
    expect(item).not.toHaveProperty('page_index');
    expect(item).not.toHaveProperty('timecode_start_s');
    expect(item).not.toHaveProperty('segment_index');
  });

  it.each([
    'drawing.svg',
    'report.html',
    'archive.zip',
    'manual.docx',
    'unknown'
  ])('retains native open-file behavior without embedding %s', (filename) => {
    expect(attachmentPreviewItem({ ...record, filename }, owner)).toBeNull();
  });

  it('never previews an external link even if its name looks like a PDF', () => {
    expect(
      attachmentPreviewItem(
        { ...record, link: 'https://example.invalid/manual.pdf' },
        owner
      )
    ).toBeNull();
    expect(
      attachmentPreviewItem({ ...record, attachment: null }, owner)
    ).toBeNull();
  });

  it.each([
    { ...record, pk: 0 },
    { ...record, pk: Number.NaN },
    { ...record, pk: 1.5 },
    { ...record, model_id: 19 },
    { ...record, model_type: 'workorder' }
  ])('refuses invalid identity or owner mismatch', (candidate) => {
    expect(attachmentPreviewItem(candidate, owner)).toBeNull();
  });

  it('does not enable a mismatched resource owner or unresolved machine', () => {
    expect(
      attachmentPreviewItem(record, {
        model_type: ModelType.workorder,
        model_id: 18
      })
    ).toBeNull();
    expect(attachmentPreviewItem(record, { ...owner, model_id: 0 })).toBeNull();
  });

  it('projects the exact native work owner for source preview and related-record navigation', () => {
    const workOwner = { model_type: ModelType.workorder, model_id: 73 };
    expect(
      attachmentPreviewItem(
        { ...record, model_type: 'workorder', model_id: 73 },
        workOwner
      )
    ).toEqual({
      attachment_id: 901,
      model_type: 'workorder',
      model_id: 73,
      work_order_id: 73,
      media_type: 'image',
      label: 'nameplate.png'
    });
  });

  it('does not infer a work record from an attachment PK or another work owner', () => {
    expect(
      attachmentPreviewItem(
        { ...record, model_type: 'workorder', model_id: 73 },
        { model_type: ModelType.workorder, model_id: 74 }
      )
    ).toBeNull();
  });
});
