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

import type { EvidenceClassification } from '@lib/types/AimmsWire.generated';
import { t } from '@lingui/core/macro';
import { Badge, Group, Stack, Text } from '@mantine/core';
import { IconLink, IconLock } from '@tabler/icons-react';

/** One displayable citation row (v1-adapted or v2 manifest-derived). */
export interface CitationDisplayEntry {
  ordinal: number;
  sourceType: string;
  available: boolean;
  asOf: string;
  sourceId?: string;
  sourceTitle?: string;
  sourceRevision?: string;
  /** true => Controlled badge; false => Uncontrolled-attachment badge;
   *  undefined (v1) => no control-class badge. */
  controlled?: boolean;
  sourceClass?: string;
  locator?: {
    page?: number | null;
    section?: string | null;
    field?: string | null;
  };
  applicability?: string;
  classification?: EvidenceClassification;
  evidenceSetId?: string;
  calculation?: string;
}

/** Human locator string; segments only when present. */
export function formatLocator(
  locator: CitationDisplayEntry['locator']
): string | null {
  if (!locator) return null;
  const parts: string[] = [];
  if (locator.page != null) parts.push(t`p. ${locator.page}`);
  if (locator.section) parts.push(`§${locator.section}`);
  if (locator.field) parts.push(t`field: ${locator.field}`);
  return parts.length ? parts.join(' · ') : null;
}

export function CitationRow({
  entry,
  anchorPrefix
}: Readonly<{ entry: CitationDisplayEntry; anchorPrefix?: string }>) {
  const stamp = entry.asOf ? new Date(entry.asOf).toLocaleString() : '';
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
  const primary =
    entry.sourceTitle || entry.sourceId || entry.sourceType || t`source`;
  const label =
    entry.sourceTitle && entry.sourceId && entry.sourceTitle !== entry.sourceId
      ? `${entry.sourceTitle} · ${entry.sourceId}`
      : primary;
  const locator = formatLocator(entry.locator);
  return (
    <Group
      key={entry.ordinal}
      gap={6}
      wrap='nowrap'
      id={anchorId}
      data-testid={`citation-row-${entry.ordinal}`}
    >
      <IconLink size={12} aria-hidden />
      <Text size='xs' c='dimmed' style={{ minWidth: 0 }}>
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
  if (citations.length === 0) {
    return null;
  }
  return (
    <Stack gap={2} data-testid='scoped-chat-citations'>
      <Text size='xs' fw={600} c='dimmed'>
        {t`Sources`}
      </Text>
      {citations.map((entry) => (
        <CitationRow
          key={entry.ordinal}
          entry={entry}
          anchorPrefix={anchorPrefix}
        />
      ))}
    </Stack>
  );
}
