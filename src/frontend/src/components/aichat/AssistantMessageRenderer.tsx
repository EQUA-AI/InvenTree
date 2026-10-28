import { t } from '@lingui/core/macro';
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  CopyButton,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
  Tooltip,
  Transition
} from '@mantine/core';
import {
  IconCheck,
  IconCopy,
  IconFileExport,
  IconRefresh,
  IconThumbDown,
  IconThumbUp
} from '@tabler/icons-react';
import { useState } from 'react';
import { api } from '../../App';
import type { ChatMessage, QuestionResolution } from '../../hooks/UseAIChat';
import { QuestionCard } from '../ai/QuestionCard';
import workspaceStyles from './AssistantWorkspace.module.css';
import { CitationList } from './CitationList';
import { ClaimEvidence } from './ClaimEvidence';
import { ContextUsedDisclosure } from './ContextUsedDisclosure';
import { EntityChips } from './EntityChips';
import { EvidenceChips } from './EvidenceChips';
import { MarkdownMessage } from './MarkdownMessage';
import { RetrievalCoverage } from './RetrievalCoverage';
import { WorkFocusChip } from './WorkFocusChip';
import type { EvidenceAnalysisAttachment } from './evidenceAnalysis';
import { composeAnswerMarkdown } from './evidenceFormat';
import { RichMessage } from './genui/RichMessage';

function formatToolDuration(durationMs?: number): string | null {
  if (durationMs === undefined || !Number.isFinite(durationMs)) return null;
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`;
  return `${(durationMs / 1000).toFixed(1)} s`;
}

/**
 * Animated typing indicator - 3 bouncing dots
 */
function TypingIndicator() {
  return (
    <Group gap='xs'>
      <Loader size='xs' />
      <Text size='sm' c='dimmed'>{t`Working…`}</Text>
    </Group>
  );
}

/**
 * Message action buttons (copy, thumbs up/down)
 */
function MessageActions({
  content,
  messageId,
  threadId,
  evidenceAnalysis,
  onRegenerate
}: Readonly<{
  content: string;
  messageId: string;
  threadId: string | null;
  evidenceAnalysis?: EvidenceAnalysisAttachment;
  onRegenerate?: () => void;
}>) {
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(null);
  // S11 (Q28): copy/export carry scope + as-of + coverage + citations,
  // composed from the persisted manifest ONLY — live copy, reload copy and
  // export are one function over one payload. The feedback SHA-256 below
  // deliberately keeps hashing the RAW content (content-addressed ledger).
  const exportable = evidenceAnalysis
    ? composeAnswerMarkdown(content, evidenceAnalysis)
    : content;

  const download = () => {
    const blob = new Blob([exportable], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `aimms-answer-${(threadId ?? 'thread').slice(0, 15)}-${messageId.slice(0, 12)}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  // Persist the verdict to the durable ledger. Freshly streamed messages
  // carry client-generated ids that never exist server-side, so the exact
  // rated content's SHA-256 rides along and the server attributes by content
  // when the id is unknown. Toggling a thumb off retracts the verdict —
  // "no verdict" is a legitimate latest state.
  const rate = (rating: 'up' | 'down') => {
    const next = feedback === rating ? null : rating;
    setFeedback(next);
    if (!threadId) {
      return;
    }
    void (async () => {
      try {
        const bytes = new TextEncoder().encode(content);
        const digest = await crypto.subtle.digest('SHA-256', bytes);
        const contentSha256 = Array.from(new Uint8Array(digest))
          .map((byte) => byte.toString(16).padStart(2, '0'))
          .join('');
        await api.post('/api/aichat/feedback/', {
          thread_id: threadId,
          message_id: messageId,
          rating: next ?? 'none',
          content_sha256: contentSha256
        });
      } catch {
        // The optimistic state stays; the ledger simply missed one verdict.
        console.debug('feedback not recorded');
      }
    })();
  };

  return (
    <Group
      gap={8}
      mt='xs'
      style={{
        flexWrap: 'wrap'
      }}
      className='message-actions'
    >
      <CopyButton value={exportable}>
        {({ copied, copy }) => (
          <Tooltip label={copied ? t`Copied!` : t`Copy`} withArrow>
            <ActionIcon
              className={workspaceStyles.control}
              data-testid='copy-ai-chat-message'
              aria-label={t`Copy answer`}
              size={32}
              variant='subtle'
              color={copied ? 'teal' : 'gray'}
              onClick={copy}
            >
              {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
            </ActionIcon>
          </Tooltip>
        )}
      </CopyButton>
      <Tooltip label={t`Good response`} withArrow>
        <ActionIcon
          className={workspaceStyles.control}
          data-testid='rate-ai-chat-message-good'
          aria-label={t`Good response`}
          size={32}
          variant='subtle'
          color={feedback === 'up' ? 'blue' : 'gray'}
          onClick={() => rate('up')}
        >
          <IconThumbUp size={14} />
        </ActionIcon>
      </Tooltip>
      <Tooltip label={t`Bad response`} withArrow>
        <ActionIcon
          className={workspaceStyles.control}
          data-testid='rate-ai-chat-message-bad'
          aria-label={t`Bad response`}
          size={32}
          variant='subtle'
          color={feedback === 'down' ? 'red' : 'gray'}
          onClick={() => rate('down')}
        >
          <IconThumbDown size={14} />
        </ActionIcon>
      </Tooltip>
      {evidenceAnalysis && (
        <Tooltip label={t`Export as Markdown`} withArrow>
          <ActionIcon
            className={workspaceStyles.control}
            data-testid='export-ai-chat-message'
            aria-label={t`Export answer as Markdown`}
            size='xs'
            variant='subtle'
            color='gray'
            onClick={download}
          >
            <IconFileExport size={14} />
          </ActionIcon>
        </Tooltip>
      )}
      {onRegenerate && (
        <Tooltip label={t`Regenerate`} withArrow>
          <ActionIcon
            className={workspaceStyles.control}
            data-testid='regenerate-ai-chat-message'
            aria-label={t`Regenerate answer`}
            size='xs'
            variant='subtle'
            color='gray'
            onClick={onRegenerate}
          >
            <IconRefresh size={14} />
          </ActionIcon>
        </Tooltip>
      )}
    </Group>
  );
}

/**
 * Single chat message component - CopilotKit style
 */
/** S11 (§8.8): the six DISTINCT no-data states; the client never converts
 *  an empty result into "no records exist" on its own. */
function noDataReasonLabel(
  reason: NonNullable<EvidenceAnalysisAttachment['no_data_reason']>,
  populationCount: number | null
): string {
  switch (reason) {
    case 'complete_population_no_matches':
      return populationCount != null
        ? t`No matching records among the ${populationCount} evaluated.`
        : t`No matching records in the fully evaluated population.`;
    case 'outside_active_selection':
      return t`This falls outside the active scope selection.`;
    case 'unauthorized_or_unavailable':
      return t`Source not available.`;
    case 'retrieval_failure':
      return t`The search could not be completed.`;
    case 'unresolved_applicability':
      return t`Could not determine which documents apply.`;
    case 'incomplete_coverage':
      return t`Coverage was incomplete; no conclusion was drawn.`;
  }
}

/** S11: the CLOSED progress-stage enum, mapped client-side to localized
 *  strings — server free text can never paint here. */
function progressStageLabel(stage: string): string {
  switch (stage) {
    case 'confirming_scope':
      return t`Confirming scope`;
    case 'reviewing_records':
      return t`Reviewing records`;
    case 'validating_evidence':
      return t`Validating evidence`;
    default:
      return t`Working`;
  }
}

export function AssistantMessageRenderer({
  message,
  threadId,
  aiHost,
  questionArmed,
  questionAnsweredLocally,
  questionResolution,
  onQuestionAnswer,
  onRegenerate
}: Readonly<{
  message: ChatMessage;
  threadId: string | null;
  aiHost: string;
  questionArmed?: boolean;
  questionAnsweredLocally?: boolean;
  questionResolution?: QuestionResolution;
  onQuestionAnswer?: (text: string) => Promise<boolean>;
  onRegenerate?: () => void;
}>) {
  const isUser = message.role === 'user';
  const facts = (
    <>
      {/* Server-observed entity chips (S28): navigate to the records
                this turn was actually about. */}
      {!isUser && !message.isStreaming && message.entities && (
        <EntityChips entities={message.entities} />
      )}
      {/* R4: server-verified media evidence (photos / video segments). */}
      {!isUser && !message.isStreaming && message.mediaEvidence && (
        <EvidenceChips items={message.mediaEvidence} />
      )}
    </>
  );
  const qualifications = (
    <>
      {/* S11: the v2 evidence-analysis block — partial banner,
                distinct no-data state, coverage, and claim-level evidence.
                Confidence is NEVER rendered here, even if redundantly sent. */}
      {!isUser && !message.isStreaming && message.evidenceAnalysis && (
        <Stack gap={6} mt={8}>
          {message.evidenceAnalysis.response_state === 'partial' && (
            <Alert
              color='yellow'
              variant='light'
              p='xs'
              data-testid='analysis-partial'
            >
              <Text size='xs'>
                {t`Partial answer — some checks did not finish.`}
                {message.evidenceAnalysis.incomplete_reasons.length
                  ? ` (${message.evidenceAnalysis.incomplete_reasons
                      .map((reason) => reason.facet)
                      .join(', ')})`
                  : ''}
              </Text>
            </Alert>
          )}
          {message.evidenceAnalysis.no_data_reason && (
            <Text
              size='xs'
              c='dimmed'
              data-testid={`no-data-${message.evidenceAnalysis.no_data_reason}`}
            >
              {noDataReasonLabel(
                message.evidenceAnalysis.no_data_reason,
                message.evidenceAnalysis.coverage?.population_count ?? null
              )}
            </Text>
          )}
          {message.evidenceAnalysis.coverage && (
            <RetrievalCoverage coverage={message.evidenceAnalysis.coverage} />
          )}
          {threadId && (
            <ClaimEvidence
              attachment={message.evidenceAnalysis}
              host={aiHost}
              threadId={threadId}
              messageId={message.id}
            />
          )}
        </Stack>
      )}
      {/* Diagnosis-rail provenance (S10): a cited answer shows its
                sources; an uncited one is visibly flagged, never implied.
                v1 only — a v2 attachment supersedes it entirely. */}
      {!isUser &&
        !message.isStreaming &&
        !message.evidenceAnalysis &&
        message.evidence !== undefined && (
          <Stack gap={4} mt={8}>
            {message.confidence && (
              <Badge size='sm' variant='outline' color='gray' w='fit-content'>
                {t`Declared confidence: ${message.confidence}`}
              </Badge>
            )}
            {message.evidence.length > 0 ? (
              <CitationList
                citations={message.evidence.map((entry, index) => ({
                  // v1 adapter: ordinals ARE array order here (the
                  // diagnosis rail has no server manifest).
                  ordinal: index + 1,
                  sourceType: entry.source_type,
                  available: true,
                  asOf: entry.as_of,
                  sourceTitle: entry.locator?.field ?? entry.source_type,
                  sourceId: entry.source_id,
                  sourceRevision: entry.source_revision,
                  locator: entry.locator
                }))}
              />
            ) : (
              <Text size='xs' c='orange' data-testid='diagnosis-uncited'>
                {t`No cited sources — not grounded in machine data.`}
              </Text>
            )}
          </Stack>
        )}
    </>
  );

  return (
    <Transition mounted transition='fade' duration={200}>
      {(styles) => (
        <Box
          component='article'
          aria-label={isUser ? t`Your message` : t`Assistant response`}
          data-message-role={message.role}
          style={{
            ...styles,
            display: 'flex',
            flexDirection: 'column',
            alignItems: isUser ? 'flex-end' : 'flex-start',
            marginBottom: 'var(--mantine-spacing-lg)',
            minWidth: 0,
            width: '100%'
          }}
        >
          {/* Message bubble (U1: theme-aware colors — no hardcoded
            light-only grays; user bubbles keep WCAG-safe white on blue) */}
          <Paper
            p={isUser ? 'sm' : 0}
            radius='md'
            style={{
              backgroundColor: isUser
                ? message.workFocus
                  ? 'var(--aimms-work-action-bg, var(--mantine-color-blue-8))'
                  : 'var(--mantine-primary-color-light)'
                : 'transparent',
              color:
                isUser && message.workFocus
                  ? 'var(--aimms-work-action-text, #ffffff)'
                  : 'var(--mantine-color-text)',
              width: isUser ? undefined : '100%',
              maxWidth: isUser ? '92%' : '100%',
              minWidth: 0,
              overflowWrap: 'anywhere'
            }}
          >
            {!isUser && (message.toolActivity?.length ?? 0) > 0 && (
              <Group gap={6} mb={6} wrap='wrap'>
                {message.toolActivity?.map((activity) => {
                  const duration = formatToolDuration(activity.durationMs);
                  return (
                    <Tooltip
                      key={activity.id}
                      label={
                        duration
                          ? `${activity.name} completed in ${duration}`
                          : activity.name
                      }
                      disabled={!duration}
                    >
                      <Badge
                        size='xs'
                        variant='light'
                        color={
                          activity.status === 'running'
                            ? 'blue'
                            : activity.status === 'ok'
                              ? 'teal'
                              : activity.status === 'denied'
                                ? 'yellow'
                                : 'red'
                        }
                        leftSection={
                          activity.status === 'running' ? (
                            <Loader size={8} />
                          ) : undefined
                        }
                      >
                        {activity.name}
                        {duration ? ` (${duration})` : ''}
                      </Badge>
                    </Tooltip>
                  );
                })}
              </Group>
            )}
            {message.content ? (
              isUser ? (
                <Text
                  size='sm'
                  style={{
                    whiteSpace: 'pre-wrap',
                    lineHeight: 1.6,
                    wordBreak: 'break-word'
                  }}
                >
                  {message.content}
                </Text>
              ) : message.question ? null : (
                <RichMessage
                  message={message}
                  narrative={<MarkdownMessage content={message.content} />}
                  facts={facts}
                  qualifications={qualifications}
                />
              )
            ) : message.isStreaming && message.progressStage ? (
              /* S11: buffered execution shows only the safe generated
                 stages (plus cancel); no provisional text ever streams. */
              <Group gap={6} wrap='nowrap'>
                <Loader size='xs' />
                <Text
                  size='xs'
                  c='dimmed'
                  aria-live='polite'
                  data-testid='analysis-progress'
                >
                  {progressStageLabel(message.progressStage)}
                </Text>
              </Group>
            ) : (
              message.isStreaming && !message.question && <TypingIndicator />
            )}
            {/* Structured question card (S22/S23): message-anchored so the
                frozen card survives reload; the singleton governs armedness. */}
            {!isUser && message.question && (
              <QuestionCard
                key={message.question.interrupt_id}
                payload={message.question}
                armed={!!questionArmed}
                answeredExternally={!!questionAnsweredLocally}
                resolution={questionResolution}
                onAnswer={(text) =>
                  onQuestionAnswer?.(text) ?? Promise.resolve(false)
                }
              />
            )}
            {!message.isStreaming && message.workFocus && (
              <WorkFocusChip focus={message.workFocus} />
            )}
            {(!message.content || message.question) && facts}
            {/* M2 PR 9 (GR-16): the content-free Context used record —
                ids and counts only, collapsed by default. */}
            {!isUser && !message.isStreaming && message.contextUsed && (
              <ContextUsedDisclosure record={message.contextUsed} />
            )}
            {(!message.content || message.question) && qualifications}
          </Paper>

          {/* Action buttons for assistant messages */}
          {!isUser && !message.isStreaming && message.content && (
            <Box>
              <MessageActions
                content={message.content}
                messageId={message.id}
                threadId={threadId}
                evidenceAnalysis={message.evidenceAnalysis}
                onRegenerate={onRegenerate}
              />
            </Box>
          )}
        </Box>
      )}
    </Transition>
  );
}
