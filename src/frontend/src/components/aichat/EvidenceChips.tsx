/**
 * Server-verified media-evidence chips under an answer (R4).
 *
 * Every chip comes from the server's media-evidence manifest — retrieval
 * hits the turn's tools actually returned, projected server-side with
 * neutral id/timecode labels. The model cannot place a chip, and clicking
 * one opens the viewer modal (model-authored links stay dead text).
 */

import { Badge, Group } from '@mantine/core';
import { IconPhoto, IconVideo } from '@tabler/icons-react';
import { useState } from 'react';

import type { MediaEvidenceItem } from '../../hooks/UseAIChat';
import { MediaEvidenceModal } from './MediaEvidenceModal';

export function EvidenceChips({
  items
}: Readonly<{ items: MediaEvidenceItem[] }>) {
  const [selected, setSelected] = useState<MediaEvidenceItem | null>(null);

  if (!items || items.length === 0) {
    return null;
  }

  return (
    <>
      <Group gap={6} mt={8} data-testid='evidence-chips'>
        {items.map((item) => {
          const key = `${item.attachment_id}:${item.segment_index}`;
          const isVideo = item.media_type === 'video_segment';
          return (
            <Badge
              key={key}
              size='sm'
              variant='light'
              color='teal'
              style={{ cursor: 'pointer' }}
              leftSection={
                isVideo ? <IconVideo size={12} /> : <IconPhoto size={12} />
              }
              onClick={() => setSelected(item)}
              data-testid={`evidence-chip-${key}`}
            >
              {item.label}
            </Badge>
          );
        })}
      </Group>
      <MediaEvidenceModal item={selected} onClose={() => setSelected(null)} />
    </>
  );
}
