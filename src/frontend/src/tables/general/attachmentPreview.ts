import { ModelType } from '@lib/enums/ModelType';
import type { MediaPreviewItem } from '../../components/aichat/evidenceLocator';

export type AttachmentPreviewRecord = {
  pk: number;
  model_type: string;
  model_id: number;
  filename: string;
  attachment: string | null;
  link: string | null;
};

export function attachmentPreviewItem(
  record: AttachmentPreviewRecord,
  owner: { model_type: ModelType; model_id: number }
): MediaPreviewItem | null {
  if (
    ![ModelType.assetmachine, ModelType.workorder].includes(owner.model_type) ||
    record.model_type !== owner.model_type ||
    record.model_id !== owner.model_id ||
    !Number.isSafeInteger(record.pk) ||
    record.pk <= 0 ||
    !Number.isSafeInteger(owner.model_id) ||
    owner.model_id <= 0 ||
    !record.attachment ||
    record.link ||
    !record.filename
  ) {
    return null;
  }

  const extension = record.filename.split('.').pop()?.toLowerCase();
  const mediaType =
    extension === 'pdf'
      ? 'document'
      : ['png', 'jpg', 'jpeg', 'webp', 'gif'].includes(extension ?? '')
        ? 'image'
        : ['mp4', 'm4v', 'mov'].includes(extension ?? '')
          ? 'video'
          : null;

  return mediaType
    ? {
        attachment_id: record.pk,
        model_type: record.model_type,
        model_id: record.model_id,
        ...(owner.model_type === ModelType.workorder
          ? { work_order_id: owner.model_id }
          : {}),
        media_type: mediaType,
        label: record.filename
      }
    : null;
}
