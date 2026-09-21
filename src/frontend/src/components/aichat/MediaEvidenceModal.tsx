/** One revision-aware viewer; source bytes are authorized again on every read. */
import { ModelType } from '@lib/enums/ModelType';
import { getDetailUrl } from '@lib/functions/Navigation';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Anchor,
  Button,
  Group,
  Loader,
  Modal,
  Stack,
  Text
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../../contexts/ApiContext';
import { generateUrl } from '../../functions/urls';
import type { MediaEvidenceItem } from '../../hooks/UseAIChat';
import { useAIChatState } from '../../states/AIChatState';
import { useEvidenceViewerState } from '../../states/EvidenceViewerState';
import { isolateDialog } from './dialogBackground';
import {
  evidenceKey,
  parseSourceMetadata,
  validDocumentPage,
  validVideoSegment
} from './evidenceLocator';
import { useAssistantNavigation } from './useAssistantNavigation';

export function GlobalEvidenceViewer() {
  const { item, close } = useEvidenceViewerState();
  return (
    <MediaEvidenceModal
      key={item ? evidenceKey(item) : 'closed'}
      item={item}
      onClose={close}
    />
  );
}

export function MediaEvidenceModal({
  item,
  onClose
}: Readonly<{ item: MediaEvidenceItem | null; onClose: () => void }>) {
  const api = useApi();
  const onNavigate = useAssistantNavigation();
  const narrow = useMediaQuery('(max-width: 48em)');
  const generation = useAIChatState((s) => s.sessionGeneration);
  const [failed, setFailed] = useState(false);
  const [exactUnavailable, setExactUnavailable] = useState(false);
  const [seekConfirmed, setSeekConfirmed] = useState(false);
  const [page, setPage] = useState(item?.page_index ?? 0);
  const [zoom, setZoom] = useState(100);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [modalElement, setModalElement] = useState<HTMLDivElement | null>(null);
  const initialSeek = useRef<number | null>(null);
  const source = useQuery({
    queryKey: [
      'ai-evidence-source',
      generation,
      item?.attachment_id,
      item?.source_revision
    ],
    enabled:
      !!item &&
      Number.isSafeInteger(item.attachment_id) &&
      item.attachment_id > 0,
    retry: false,
    staleTime: 0,
    gcTime: 0,
    queryFn: async ({ signal }) => {
      const { data } = await api.get(
        `/api/aichat/evidence/media/${item!.attachment_id}/metadata/`,
        {
          params: item?.source_revision
            ? { revision: item.source_revision }
            : {},
          signal
        }
      );
      return parseSourceMetadata(data, item?.source_revision);
    }
  });
  useEffect(() => {
    if (!item || !modalElement) return;
    // Mantine can share one portal among several dialogs; isolate this modal's
    // root (including its overlay), not the entire shared portal container.
    const root = modalElement.closest<HTMLElement>(
      '[data-testid="media-evidence-modal"]'
    );
    return isolateDialog(root ?? modalElement);
  }, [item, modalElement]);
  useEffect(() => {
    const video = videoRef.current;
    return () => {
      if (video) {
        video.pause();
        video.removeAttribute('src');
        video.load();
      }
    };
  }, [source.data, item]);
  if (!item) return null;
  const metadata = source.data;
  const mediaUrl = metadata
    ? generateUrl(
        `/api/aichat/evidence/media/${item.attachment_id}/?revision=${metadata.revision}`
      )
    : '';
  const isPdf = metadata?.content_type === 'application/pdf';
  const isVideo = metadata?.content_type.startsWith('video/');
  const targetModel = item.work_order_id
    ? ModelType.workorder
    : item.model_type === 'assetmachine'
      ? ModelType.assetmachine
      : item.model_type === 'part'
        ? ModelType.part
        : null;
  const targetPk = item.work_order_id ?? item.model_id;
  const detailUrl =
    targetModel && targetPk && Number.isSafeInteger(targetPk) && targetPk > 0
      ? getDetailUrl(targetModel, targetPk)
      : null;
  const displayPage = validDocumentPage(page, metadata?.page_count) ? page : 0;
  const exactPage = validDocumentPage(item.page_index, metadata?.page_count);
  const unavailable = failed || source.isError;
  return (
    <Modal
      ref={setModalElement}
      opened
      onClose={onClose}
      returnFocus={false}
      title={item.label}
      size='80%'
      fullScreen={narrow}
      zIndex={3000}
      data-testid='media-evidence-modal'
    >
      <Stack gap='sm'>
        <Text size='xs' c='dimmed'>
          {item.source_revision
            ? t`Revision: ${item.source_revision}`
            : t`Unversioned citation — showing the currently authorized source`}
        </Text>
        {unavailable ? (
          <Alert
            color='yellow'
            data-testid='media-evidence-unavailable'
          >{t`Source unavailable. Access may have changed, or the cited revision is no longer available.`}</Alert>
        ) : source.isPending ? (
          <Loader aria-label={t`Loading source`} />
        ) : (
          metadata && (
            <>
              {(exactUnavailable || (isPdf && !exactPage)) && (
                <Alert color='yellow'>
                  {isPdf
                    ? t`Exact page unavailable. The authorized source is shown below.`
                    : t`Exact video segment unavailable. You can play the authorized source from the beginning.`}
                </Alert>
              )}
              {isPdf ? (
                <>
                  <Group>
                    <Button
                      variant='default'
                      disabled={displayPage <= 0}
                      onClick={() => setPage(displayPage - 1)}
                    >{t`Previous page`}</Button>
                    <Text>
                      {t`Page ${displayPage + 1} of ${metadata.page_count ?? '?'}`}
                      {metadata.page_labels?.[displayPage]
                        ? ` · ${metadata.page_labels[displayPage]}`
                        : ''}
                    </Text>
                    <Button
                      variant='default'
                      disabled={
                        !metadata.page_count ||
                        displayPage + 1 >= metadata.page_count
                      }
                      onClick={() => setPage(displayPage + 1)}
                    >{t`Next page`}</Button>
                  </Group>
                  <object
                    key={`${page}:${zoom}`}
                    data={`${mediaUrl}#page=${(validDocumentPage(page, metadata.page_count) ? page : 0) + 1}&zoom=${zoom}`}
                    type='application/pdf'
                    aria-label={t`Document preview`}
                    style={{ width: '100%', height: '65vh' }}
                  >
                    <Text>{t`This browser cannot display the document. Open the original to use your document reader.`}</Text>
                  </object>
                </>
              ) : isVideo ? (
                <>
                  {item.timecode_start_s != null && (
                    <Text size='sm'>
                      {t`Cited time`}: {item.timecode_start_s}s
                      {item.timecode_end_s != null
                        ? ` – ${item.timecode_end_s}s`
                        : ''}
                      {seekConfirmed ? ` · ${t`Position verified`}` : ''}
                    </Text>
                  )}
                  {/* biome-ignore lint/a11y/useMediaCaption: Uploaded evidence has no caption track; its absence is disclosed below. */}
                  <video
                    ref={videoRef}
                    controls
                    preload='metadata'
                    style={{ width: '100%', maxHeight: '65vh' }}
                    src={mediaUrl}
                    onLoadedMetadata={(event) => {
                      const video = event.currentTarget;
                      if (
                        !validVideoSegment(
                          item.timecode_start_s,
                          item.timecode_end_s,
                          video.duration
                        )
                      ) {
                        setExactUnavailable(true);
                        return;
                      }
                      const start = item.timecode_start_s!;
                      if (start === 0 && video.currentTime === 0)
                        setSeekConfirmed(true);
                      else {
                        initialSeek.current = start;
                        video.currentTime = start;
                      }
                    }}
                    onSeeked={(event) => {
                      if (initialSeek.current === null) return;
                      // Half-second tolerance accounts for keyframe seeking in uploaded videos.
                      const confirmed =
                        Math.abs(
                          event.currentTarget.currentTime - initialSeek.current
                        ) <= 0.5;
                      setSeekConfirmed(confirmed);
                      setExactUnavailable(!confirmed);
                      initialSeek.current = null;
                    }}
                    onError={() => setFailed(true)}
                  />
                  <Text
                    size='xs'
                    c='dimmed'
                  >{t`Captions and a transcript are not available for this source.`}</Text>
                </>
              ) : (
                <div style={{ overflow: 'auto', maxHeight: '65vh' }}>
                  <img
                    src={mediaUrl}
                    alt={item.label}
                    style={{
                      width: `${zoom}%`,
                      maxWidth: 'none',
                      objectFit: 'contain'
                    }}
                    onError={() => setFailed(true)}
                  />
                </div>
              )}
              <Group justify='space-between'>
                {!isVideo && (
                  <Group>
                    <Button
                      variant='default'
                      disabled={zoom <= 50}
                      onClick={() => setZoom(zoom - 25)}
                    >{t`Zoom out`}</Button>
                    <Button
                      variant='default'
                      onClick={() => setZoom(100)}
                    >{t`Fit`}</Button>
                    <Button
                      variant='default'
                      disabled={zoom >= 300}
                      onClick={() => setZoom(zoom + 25)}
                    >{t`Zoom in`}</Button>
                  </Group>
                )}
                <Anchor
                  href={mediaUrl}
                  target='_blank'
                  rel='noopener noreferrer'
                >{t`Open original`}</Anchor>
              </Group>
            </>
          )
        )}
        {detailUrl && (
          <Anchor
            component={Link}
            to={detailUrl}
            onClick={(event) => {
              onNavigate(event);
              if (
                !event.defaultPrevented &&
                event.button === 0 &&
                !event.ctrlKey &&
                !event.metaKey &&
                !event.shiftKey &&
                !event.altKey
              )
                onClose();
            }}
          >{t`Open related record`}</Anchor>
        )}
      </Stack>
    </Modal>
  );
}
