/**
 * Evidence viewer modal (R4): plays the photo or video segment a chip points
 * at, seeked to the segment's start. Media rides the authenticated
 * Range-aware evidence stream endpoint — never a /media path and never a
 * model-authored URL. The title is the server-built label only; uploader
 * filenames deliberately never render here.
 */

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { ModelType } from '@lib/enums/ModelType';
import { apiUrl } from '@lib/functions/Api';
import { getDetailUrl, navigateToLink } from '@lib/functions/Navigation';
import { t } from '@lingui/core/macro';
import { Alert, Button, Group, Modal, Stack } from '@mantine/core';
import { IconAlertTriangle, IconExternalLink } from '@tabler/icons-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useApi } from '../../contexts/ApiContext';
import { generateUrl } from '../../functions/urls';
import type { MediaEvidenceItem } from '../../hooks/UseAIChat';

export function MediaEvidenceModal({
  item,
  onClose
}: Readonly<{ item: MediaEvidenceItem | null; onClose: () => void }>) {
  const api = useApi();
  const navigate = useNavigate();
  const [unavailable, setUnavailable] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const mediaUrl = useMemo(
    () =>
      item
        ? generateUrl(`/api/aichat/evidence/media/${item.attachment_id}/`)
        : '',
    [item]
  );

  useEffect(() => {
    setUnavailable(false);
    if (!item) return;
    // Existence probe via the (pre-existing, authenticated) attachment API;
    // a 404 degrades to the value-free alert while keeping navigation.
    api
      .get(apiUrl(ApiEndpoints.attachment_list, item.attachment_id))
      .catch(() => setUnavailable(true));
  }, [api, item]);

  if (!item) return null;

  const isVideo = item.media_type === 'video_segment';
  const targetModel =
    item.work_order_id != null
      ? ModelType.workorder
      : item.model_type === 'assetmachine'
        ? ModelType.assetmachine
        : undefined;
  const targetPk =
    item.work_order_id != null ? item.work_order_id : (item.model_id ?? null);
  const detailUrl =
    targetModel && targetPk ? getDetailUrl(targetModel, targetPk) : '';

  return (
    <Modal
      opened
      onClose={onClose}
      title={item.label}
      size='lg'
      zIndex={3000}
      data-testid='media-evidence-modal'
    >
      <Stack gap='sm'>
        {unavailable ? (
          <Alert
            color='yellow'
            icon={<IconAlertTriangle size={16} />}
            data-testid='media-evidence-unavailable'
          >
            {t`Evidence file is unavailable`}
          </Alert>
        ) : isVideo ? (
          // biome-ignore lint/a11y/useMediaCaption: R4 evidence uploads do not provide caption tracks.
          <video
            ref={videoRef}
            controls
            preload='metadata'
            style={{ width: '100%', maxHeight: '60vh' }}
            src={mediaUrl}
            onLoadedMetadata={() => {
              const start = item.timecode_start_s ?? 0;
              if (videoRef.current && start > 0) {
                videoRef.current.currentTime = start;
              }
            }}
            onError={() => setUnavailable(true)}
          />
        ) : (
          <img
            src={mediaUrl}
            alt={item.label}
            style={{ width: '100%', maxHeight: '60vh', objectFit: 'contain' }}
            onError={() => setUnavailable(true)}
          />
        )}
        <Group justify='flex-end'>
          {detailUrl && (
            <Button
              variant='light'
              size='xs'
              leftSection={<IconExternalLink size={14} />}
              onClick={(event) => {
                navigateToLink(detailUrl, navigate, event);
                onClose();
              }}
            >
              {targetModel === ModelType.workorder
                ? t`Open work order`
                : t`Open machine`}
            </Button>
          )}
        </Group>
      </Stack>
    </Modal>
  );
}
