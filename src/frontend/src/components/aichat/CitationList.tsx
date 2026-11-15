/**
 * Numbered citations with source labels and as-of times.
 *
 * Citations are re-filtered server-side against the viewer's current
 * authorization: a revoked source renders as unavailable, never as content.
 *
 * S11: ordinals are SERVER-SUPPLIED for v2 evidence-analysis answers — they
 * match the literal `[n]` markers the server rendered into the prose, so
 * the client never derives them from array order. The v1 diagnosis adapter
 * supplies `ordinal: index + 1` explicitly, keeping v1 output identical.
 */

import { t } from '@lingui/core/macro';
import { Badge, Button, Group, Stack, Text } from '@mantine/core';
import { IconLink, IconLock } from '@tabler/icons-react';
import { useEvidenceViewerState } from '../../states/EvidenceViewerState';
import { type CitationDisplayEntry, formatLocator } from './citationFormat';
import {
  localManualCitation,
  nativeDocumentCitation
} from './documentCitation';

export { formatLocator } from './citationFormat';
export type { CitationDisplayEntry } from './citationFormat';

export function CitationRow({
  entry,
  anchorPrefix,
  timeZone
}: Readonly<{
  entry: CitationDisplayEntry;
  anchorPrefix?: string;
  timeZone?: 'UTC';
}>) {
  const stamp = entry.asOf
    ? new Date(entry.asOf).toLocaleString(
        undefined,
        timeZone ? { timeZone, timeZoneName: 'short' } : undefined
      )
    : '';
  const anchorId = anchorPrefix
    ? `${anchorPrefix}-cite-${entry.ordinal}`
    : undefined;
  if (!entry.available) {
    // One rendering for EVERY unavailable cause: revoked and deleted are
    // indistinguishable by design.
    return (
      <Group
        key={entry.ordinal}
        gap={4}
        wrap='nowrap'
        id={anchorId}
        data-testid={`citation-row-${entry.ordinal}`}
      >
        <IconLock size={12} aria-hidden />
        <Text size='xs' c='dimmed'>
          [{entry.ordinal}] {t`Source unavailable`} — {stamp}
        </Text>
      </Group>
    );
  }
  const manual = localManualCitation(entry);
  const title =
    manual && entry.sourceTitle?.startsWith('/media/')
      ? t`Related manual`
      : entry.sourceTitle;
  const primary = title || entry.sourceId || entry.sourceType || t`source`;
  const label =
    title && entry.sourceId && title !== entry.sourceId
      ? `${title} · ${entry.sourceId}`
      : primary;
  const locator = formatLocator(
    manual
      ? { ...entry.locator, field: null, page: manual.pageIndex + 1 }
      : entry.locator
  );
  return (
    <Group
      key={entry.ordinal}
      gap={6}
      wrap='nowrap'
      id={anchorId}
      data-testid={`citation-row-${entry.ordinal}`}
    >
      <IconLink size={12} aria-hidden />
      <Text
        size='xs'
        c='dimmed'
        style={{ minWidth: 0, overflowWrap: 'anywhere' }}
      >
        [{entry.ordinal}] {label}
        {entry.sourceRevision ? ` · rev ${entry.sourceRevision}` : ''}
        {locator ? ` · ${locator}` : ''}
        {stamp ? ` — ${t`as of`} ${stamp}` : ''}
      </Text>
      {entry.controlled === true && (
        <Badge size='xs' variant='outline' color='teal'>
          {t`Controlled`}
        </Badge>
      )}
      {entry.controlled === false && (
        <Badge size='xs' variant='outline' color='yellow'>
          {t`Uncontrolled attachment`}
        </Badge>
      )}
      {entry.classification && (
        <Badge size='xs' variant='light' color='gray'>
          {entry.classification}
        </Badge>
      )}
    </Group>
  );
}

export function CitationList({
  citations,
  anchorPrefix
}: Readonly<{ citations: CitationDisplayEntry[]; anchorPrefix?: string }>) {
  const openEvidence = useEvidenceViewerState((state) => state.open);
  if (citations.length === 0) {
    return null;
  }
  return (
    <Stack gap={2} data-testid='scoped-chat-citations'>
      <Text size='xs' fw={600} c='dimmed'>
        {t`Sources`}
      </Text>
      {citations.map((entry) => {
        const document = nativeDocumentCitation(entry);
        return (
          <Stack key={entry.ordinal} gap={2}>
            <CitationRow entry={entry} anchorPrefix={anchorPrefix} />
            {document && (
              <Button
                size='compact-xs'
                variant='light'
                w='fit-content'
                aria-label={t`Open related manual ${document.attachmentId}, page ${document.pageIndex + 1}`}
                onClick={(event) =>
                  openEvidence(
                    {
                      attachment_id: document.attachmentId,
                      model_type: document.modelType,
                      model_id: document.modelId,
                      source_revision: document.revision,
                      page_index: document.pageIndex,
                      media_type: 'document',
                      label: t`Related manual ${document.attachmentId} · p. ${document.pageIndex + 1}`
                    },
                    event.currentTarget
                  )
                }
              >
                {t`Open cited PDF · p. ${document.pageIndex + 1}`}
              </Button>
            )}
          </Stack>
        );
      })}
    </Stack>
  );
}
