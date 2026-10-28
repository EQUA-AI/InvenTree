/** Only server-authorized native citations can supply a revision-bound PDF target. */
export interface DocumentCitationSource {
  available: boolean;
  sourceType: string;
  sourceId?: string;
  sourceRevision?: string;
  locator?: { field?: string | null; page?: number | null };
}

export interface NativeDocumentCitation {
  attachmentId: number;
  modelType: string;
  modelId: number;
  revision: string;
  pageIndex: number;
}

export function localManualCitation(
  source: DocumentCitationSource
): NativeDocumentCitation | null {
  if (
    !source.available ||
    source.sourceType !== 'related_attached_manual' ||
    !/^[1-9]\d*$/.test(source.sourceId ?? '') ||
    !/^[a-f0-9]{64}$/.test(source.sourceRevision ?? '')
  )
    return null;
  const field = source.locator?.field;
  if (
    !field?.startsWith('/media/attachments/') ||
    field.includes('\\') ||
    /%(?:2f|5c|00)/i.test(field)
  )
    return null;
  try {
    const url = new URL(field, 'https://native-citation.invalid');
    const path = url.pathname.match(
      /^\/media\/attachments\/(assetmachine|part|workorder|workorderstepexecution|repairpacket|assetcomponent|kanbancard)\/([1-9]\d*)\/([^/]+)$/
    );
    const page = url.hash.match(/^#page=([1-9]\d*)$/);
    if (
      url.origin !== 'https://native-citation.invalid' ||
      url.search ||
      !path ||
      !page ||
      !decodeURIComponent(path[3]).toLowerCase().endsWith('.pdf')
    )
      return null;
    const attachmentId = Number(source.sourceId);
    const modelId = Number(path[2]);
    const pageNumber = Number(page[1]);
    if (
      ![attachmentId, modelId, pageNumber].every(Number.isSafeInteger) ||
      pageNumber > 10000 ||
      (source.locator?.page != null && source.locator.page !== pageNumber)
    )
      return null;
    return {
      attachmentId,
      modelType: path[1],
      modelId,
      revision: source.sourceRevision!,
      pageIndex: pageNumber - 1
    };
  } catch {
    return null;
  }
}

/** The existing authenticated viewer supports these indexed attachment owners. */
export function nativeDocumentCitation(
  source: DocumentCitationSource
): NativeDocumentCitation | null {
  const citation = localManualCitation(source);
  return citation &&
    ['assetmachine', 'part', 'workorder', 'workorderstepexecution'].includes(
      citation.modelType
    )
    ? citation
    : null;
}
