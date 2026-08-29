/**
 * S11: claim-level evidence, grouped by citation ordinal (§8.8).
 *
 * Each `[n]` marker in the server-rendered prose has one expandable row
 * here (the ToolTraceDisclosure pattern). Expansion shows source type,
 * title/reference, revision, locator, control class, as-of, applicability,
 * and the citing claims with their evidence classification — never model
 * confidence, prompts, or tool arguments.
 *
 * Aggregate citations (an `evidence_set_id`) additionally expand to the
 * calculation and the exact member list through the owner-safe paginated
 * endpoint. Members are reauthorized live per request (react-query with
 * `refetchOnMount: 'always'` + a short gcTime): an unavailable member — or
 * an unavailable whole set — gets ONE indistinguishable rendering,
 * whatever the cause.
 */

import type {
  ClaimPayload,
  EvidenceSetMember
} from '@lib/types/AimmsWire.generated';
import { t } from '@lingui/core/macro';
import {
  Badge,
  Button,
  Collapse,
  Group,
  Loader,
  Stack,
  Text,
  UnstyledButton
} from '@mantine/core';
import {
  IconChevronDown,
  IconChevronRight,
  IconLock
} from '@tabler/icons-react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { CitationRow } from './CitationList';
import type { EvidenceAnalysisAttachment } from './evidenceAnalysis';
import { citationDisplayFromManifest } from './evidenceFormat';

interface EvidenceSetPageData {
  members: EvidenceSetMember[];
  population_count: number;
  complete: boolean;
  next_cursor: string | null;
}

async function fetchEvidenceSetPage(
  host: string,
  threadId: string,
  setId: string,
  cursor: string | null
): Promise<EvidenceSetPageData> {
  const params = new URLSearchParams({ limit: '25' });
  if (cursor) params.set('cursor', cursor);
  const response = await fetch(
    `${host}/threads/${threadId}/evidence-sets/${setId}/members?${params}`,
    { credentials: 'include' }
  );
  if (!response.ok) {
    throw new Error(`evidence set unavailable (${response.status})`);
  }
  return (await response.json()) as EvidenceSetPageData;
}

function EvidenceSetMembers({
  host,
  threadId,
  setId,
  expanded
}: Readonly<{
  host: string;
  threadId: string;
  setId: string;
  expanded: boolean;
}>) {
  const query = useInfiniteQuery({
    queryKey: ['ai-evidence-set', threadId, setId],
    queryFn: ({ pageParam }) =>
      fetchEvidenceSetPage(host, threadId, setId, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: expanded,
    // Per-expansion LIVE reauthorization: never serve a cached member list
    // across a fresh expansion.
    refetchOnMount: 'always',
    gcTime: 30_000,
    retry: false
  });

  if (!expanded) return null;
  if (query.isLoading) {
    return <Loader size='xs' aria-label={t`Loading evidence members`} />;
  }
  if (query.isError || !query.data) {
    // ONE neutral state for every cause (404, revoked, failure).
    return (
      <Text size='xs' c='dimmed' data-testid='evidence-set-unavailable'>
        {t`Evidence details are not available.`}
      </Text>
    );
  }
  const pages = query.data.pages;
  const members = pages.flatMap((page) => page.members);
  const population = pages[0]?.population_count ?? members.length;
  const complete = pages[pages.length - 1]?.complete ?? false;
  return (
    <Stack
      gap={2}
      component='ul'
      style={{ listStyle: 'none', margin: 0, padding: 0 }}
    >
      {members.map((member) => (
        <Group
          key={member.member_index}
          gap={4}
          wrap='nowrap'
          component='li'
          data-testid={`evidence-member-${member.member_index}`}
        >
          {member.available ? (
            <Text size='xs' c='dimmed'>
              {member.member_index}. {member.label ?? member.source_object_id}
            </Text>
          ) : (
            <>
              <IconLock size={12} aria-hidden />
              <Text size='xs' c='dimmed'>
                {member.member_index}. {t`Not available`}
              </Text>
            </>
          )}
        </Group>
      ))}
      <Group gap={8}>
        <Text size='xs' c='dimmed' data-testid='evidence-set-footer'>
          {t`Showing ${members.length ? 1 : 0}–${members.length} of ${population}`}
        </Text>
        {query.hasNextPage && (
          <Button
            size='compact-xs'
            variant='subtle'
            onClick={() => query.fetchNextPage()}
            loading={query.isFetchingNextPage}
          >
            {t`Show more`}
          </Button>
        )}
      </Group>
      {!complete && !query.hasNextPage && (
        <Text size='xs' c='dimmed'>
          {t`Record-level expansion does not cover the full evaluated population.`}
        </Text>
      )}
    </Stack>
  );
}

function OrdinalDisclosure({
  attachment,
  ordinal,
  claims,
  host,
  threadId,
  messageId
}: Readonly<{
  attachment: EvidenceAnalysisAttachment;
  ordinal: number;
  claims: ClaimPayload[];
  host: string;
  threadId: string;
  messageId: string;
}>) {
  const [open, setOpen] = useState(false);
  const entry = attachment.citations.find(
    (citation) => citation.ordinal === ordinal
  );
  if (!entry) return null;
  const display = citationDisplayFromManifest(entry);
  const panelId = `${messageId}-evidence-${ordinal}`;
  return (
    <Stack gap={2}>
      <UnstyledButton
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={t`Show evidence for citation ${ordinal}`}
        data-testid={`claim-evidence-toggle-${ordinal}`}
      >
        <Group gap={4} wrap='nowrap'>
          {open ? (
            <IconChevronDown size={12} aria-hidden />
          ) : (
            <IconChevronRight size={12} aria-hidden />
          )}
          <CitationRow entry={display} anchorPrefix={messageId} />
        </Group>
      </UnstyledButton>
      <Collapse expanded={open} id={panelId}>
        <Stack gap={4} pl='md'>
          <Text size='xs' c='dimmed'>
            {t`Source type`}: {display.sourceType}
            {display.applicability
              ? ` · ${t`applicability`}: ${display.applicability}`
              : ''}
          </Text>
          {display.calculation && (
            <Text size='xs' c='dimmed'>
              {t`Calculation`}: {display.calculation}
            </Text>
          )}
          {claims.map((claim) => (
            <Group key={claim.claim_id} gap={4} wrap='nowrap'>
              <Badge size='xs' variant='light' color='gray'>
                {claim.evidence_classification}
              </Badge>
              <Text size='xs' c='dimmed'>
                {claim.claim_type} ({claim.claim_role})
              </Text>
            </Group>
          ))}
          {display.evidenceSetId && (
            <EvidenceSetMembers
              host={host}
              threadId={threadId}
              setId={display.evidenceSetId}
              expanded={open}
            />
          )}
        </Stack>
      </Collapse>
    </Stack>
  );
}

export function ClaimEvidence({
  attachment,
  host,
  threadId,
  messageId
}: Readonly<{
  attachment: EvidenceAnalysisAttachment;
  host: string;
  threadId: string;
  messageId: string;
}>) {
  if (attachment.citations.length === 0) return null;
  const claimsByOrdinal = new Map<number, ClaimPayload[]>();
  for (const claim of attachment.claims) {
    for (const ordinal of claim.citation_ordinals) {
      const bucket = claimsByOrdinal.get(ordinal) ?? [];
      bucket.push(claim);
      claimsByOrdinal.set(ordinal, bucket);
    }
  }
  return (
    <Stack gap={2} data-testid='claim-evidence'>
      <Text size='xs' fw={600} c='dimmed'>
        {t`Sources`}
      </Text>
      {attachment.citations.map((entry) => (
        <OrdinalDisclosure
          key={entry.ordinal}
          attachment={attachment}
          ordinal={entry.ordinal}
          claims={claimsByOrdinal.get(entry.ordinal) ?? []}
          host={host}
          threadId={threadId}
          messageId={messageId}
        />
      ))}
    </Stack>
  );
}
