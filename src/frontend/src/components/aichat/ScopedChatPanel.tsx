/**
 * "Ask AIMMS" panel pinned to exactly one authorized record (Feature #14).
 *
 * A reading and drafting surface, never a command surface: answers come from
 * server-authorized read-only tools with citations and as-of times, and any
 * action intent becomes a typed proposal confirmed on the governed rail.
 * Free-text LLM turns arrive with the scoped Q&A workflow; until then the
 * grounded question buttons below exercise the same governed tool authority.
 */

import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  ScrollArea,
  Stack,
  Text
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconClockPause,
  IconPlayerPlay
} from '@tabler/icons-react';
import { useCallback, useEffect, useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';

import { api } from '../../App';
import { type ScopedChatTurn, useScopedChat } from '../../hooks/UseScopedChat';
import {
  type ChatActionProposalPayload,
  ProposalCard
} from '../ai/ChatActionProposals';
import { CitationList } from './CitationList';
import { ScopeChip } from './ScopeChip';
import { ToolTraceDisclosure } from './ToolTraceDisclosure';

interface QuickQuestion {
  tool: string;
  label: string;
  arguments?: Record<string, unknown>;
}

function ReadinessResult({
  result
}: Readonly<{ result: Record<string, any> }>) {
  const blockers: any[] = result.blockers ?? [];
  return (
    <Stack gap={4}>
      <Group gap='xs'>
        <Badge color={result.ready ? 'teal' : 'red'} size='sm'>
          {result.ready ? t`Ready` : t`Blocked`}
        </Badge>
        <Text size='xs' c='dimmed'>
          {t`Action`}: {result.action} · {t`evaluated`}{' '}
          {new Date(result.evaluated_at).toLocaleString()}
        </Text>
      </Group>
      {blockers.map((blocker) => (
        <Group
          key={`${blocker.code}-${blocker.object_id}`}
          gap={4}
          wrap='nowrap'
        >
          <Badge size='xs' color='red' variant='light'>
            {blocker.code}
          </Badge>
          <Text size='xs'>{blocker.message}</Text>
        </Group>
      ))}
    </Stack>
  );
}

function SummaryResult({ result }: Readonly<{ result: Record<string, any> }>) {
  const summary: Record<string, any> = result.summary ?? {};
  return (
    <Stack gap={2}>
      {Object.entries(summary).map(([field, value]) => (
        <Group key={field} gap={6} wrap='nowrap'>
          <Text size='xs' c='dimmed' style={{ minWidth: 130 }}>
            {field}
          </Text>
          <Text size='xs'>{value == null ? '—' : String(value)}</Text>
        </Group>
      ))}
    </Stack>
  );
}

function EventsResult({ result }: Readonly<{ result: Record<string, any> }>) {
  const events: any[] = result.events ?? [];
  if (events.length === 0) {
    return <Text size='xs'>{t`No lifecycle events recorded yet.`}</Text>;
  }
  return (
    <Stack gap={2}>
      {events.map((event) => (
        <Text size='xs' key={event.correlation_id + event.created_at}>
          {new Date(event.created_at).toLocaleString()} · {event.event_type}
          {event.from_status
            ? ` (${event.from_status} → ${event.to_status})`
            : ''}
          {event.reason ? ` — ${event.reason}` : ''}
        </Text>
      ))}
      {result.truncated && (
        <Text size='xs' c='dimmed'>
          {t`Showing`} {events.length} / {result.total}
        </Text>
      )}
    </Stack>
  );
}

function StepsResult({ result }: Readonly<{ result: Record<string, any> }>) {
  if (!result.application) {
    return <Text size='xs'>{t`No procedure has been applied.`}</Text>;
  }
  const steps: any[] = result.steps ?? [];
  return (
    <Stack gap={2}>
      {steps.map((step) => (
        <Group key={step.step_key} gap={6} wrap='nowrap'>
          <Badge size='xs' variant='light'>
            {step.status}
          </Badge>
          <Text size='xs'>
            {step.sequence}. {step.title || step.step_type}
            {step.required ? ' *' : ''}
          </Text>
        </Group>
      ))}
      {result.truncated && (
        <Text size='xs' c='dimmed'>
          {t`Showing`} {steps.length} / {result.total}
        </Text>
      )}
    </Stack>
  );
}

function KitResult({ result }: Readonly<{ result: Record<string, any> }>) {
  if (!result.kit) {
    return <Text size='xs'>{t`No job kit exists for this work order.`}</Text>;
  }
  const kit = result.kit;
  return (
    <Text size='xs'>
      {t`Kit status`}: {kit.status} · {t`lines`}: {kit.line_count} ·{' '}
      {t`open shortages`}: {kit.open_shortages}
    </Text>
  );
}

function TurnResult({ turn }: Readonly<{ turn: ScopedChatTurn }>) {
  if (turn.error) {
    return (
      <Alert color='red' p='xs' icon={<IconAlertTriangle size={14} />}>
        <Text size='xs'>{turn.error}</Text>
      </Alert>
    );
  }
  const envelope = turn.envelope;
  if (!envelope) {
    return null;
  }
  if (!envelope.authorized) {
    return (
      <Alert color='yellow' p='xs'>
        <Text size='xs'>{t`Not authorized for this record right now.`}</Text>
      </Alert>
    );
  }
  const result = envelope.result ?? {};
  let body: React.ReactNode;
  switch (envelope.tool) {
    case 'work_order_readiness':
      body = <ReadinessResult result={result} />;
      break;
    case 'work_order_summary':
      body = <SummaryResult result={result} />;
      break;
    case 'work_order_events_page':
      body = <EventsResult result={result} />;
      break;
    case 'work_order_steps':
      body = <StepsResult result={result} />;
      break;
    case 'work_order_kit_status':
      body = <KitResult result={result} />;
      break;
    default:
      body = (
        <Text size='xs' style={{ whiteSpace: 'pre-wrap' }}>
          {JSON.stringify(result, null, 2)}
        </Text>
      );
  }
  return (
    <Stack gap={4}>
      {body}
      <Text size='xs' c='dimmed'>
        {t`as of`} {new Date(envelope.as_of).toLocaleString()}
      </Text>
    </Stack>
  );
}

function quickQuestions(): QuickQuestion[] {
  return [
    { tool: 'work_order_summary', label: t`Summary` },
    {
      tool: 'work_order_readiness',
      label: t`Why blocked?`,
      arguments: { action: 'start' }
    },
    { tool: 'work_order_steps', label: t`Procedure steps` },
    { tool: 'work_order_kit_status', label: t`Kit status` },
    { tool: 'work_order_events_page', label: t`History` }
  ];
}

export function ScopedChatPanel({
  contextType,
  objectId
}: Readonly<{
  contextType: string;
  objectId: string | number;
}>) {
  const scoped = useScopedChat({ contextType, objectId });
  const [proposals, setProposals] = useState<ChatActionProposalPayload[]>([]);
  const [proposalError, setProposalError] = useState<string | null>(null);

  const refreshProposals = useCallback(async () => {
    try {
      const response = await api.get(apiUrl(ApiEndpoints.aichat_proposal_list));
      const rows: ChatActionProposalPayload[] = response.data?.results ?? [];
      setProposals(
        rows.filter((row) => String(row.work_order_id) === String(objectId))
      );
    } catch {
      setProposals([]);
    }
  }, [objectId]);

  useEffect(() => {
    void refreshProposals();
  }, [refreshProposals]);

  const draftProposal = useCallback(
    async (actionType: 'work_order.hold' | 'work_order.resume') => {
      setProposalError(null);
      try {
        const conversation = await scoped.openConversation();
        await api.post(apiUrl(ApiEndpoints.aichat_proposal_list), {
          action_type: actionType,
          work_order_id: Number(objectId),
          reason: t`Drafted from scoped chat`,
          thread_id: conversation.ai_thread_id
        });
        await refreshProposals();
      } catch (error: any) {
        setProposalError(
          error?.response?.data?.error ?? 'PROPOSAL_REQUEST_FAILED'
        );
      }
    },
    [scoped, objectId, refreshProposals]
  );

  if (scoped.contextQuery.isLoading) {
    return (
      <Group justify='center' p='md'>
        <Loader size='sm' aria-label={t`Resolving record context`} />
      </Group>
    );
  }

  if (scoped.unavailable || !scoped.context) {
    return (
      <Alert color='gray' icon={<IconAlertTriangle size={16} />}>
        <Text size='sm'>
          {t`Scoped chat is not available for this record.`}
        </Text>
      </Alert>
    );
  }

  const context = scoped.context;
  const revoked = scoped.conversation?.context_state === 'revoked';
  const canPropose = scoped.capabilities.includes('propose_hold');

  return (
    <Stack gap='sm' data-testid='scoped-chat-panel'>
      <ScopeChip
        label={context.display_label}
        asOf={context.as_of}
        revoked={revoked}
      />
      {revoked && (
        <Alert color='red' p='xs'>
          <Text size='xs'>
            {t`Access to this record was revoked; the conversation is read only.`}
          </Text>
        </Alert>
      )}
      <Group gap='xs' wrap='wrap'>
        {quickQuestions()
          .filter((question) => context.tools.includes(question.tool))
          .map((question) => (
            <Button
              key={question.tool}
              size='xs'
              variant='light'
              disabled={scoped.busy}
              onClick={() =>
                void scoped.invokeTool(question.tool, question.arguments)
              }
              data-testid={`scoped-chat-ask-${question.tool}`}
            >
              {question.label}
            </Button>
          ))}
      </Group>
      <ScrollArea.Autosize mah={420}>
        <Stack gap='xs' aria-live='polite'>
          {scoped.turns.length === 0 && (
            <Text size='xs' c='dimmed'>
              {t`Ask a grounded question about this record. Every answer cites its source and as-of time.`}
            </Text>
          )}
          {scoped.turns.map((turn) => (
            <Card key={turn.turnKey} withBorder radius='md' p='xs'>
              <TurnResult turn={turn} />
            </Card>
          ))}
        </Stack>
      </ScrollArea.Autosize>
      <CitationList citations={scoped.citations} />
      <ToolTraceDisclosure rows={scoped.toolTrace} />
      {canPropose && (
        <>
          <Divider />
          <Group gap='xs'>
            <Button
              size='xs'
              variant='light'
              color='orange'
              leftSection={<IconClockPause size={14} />}
              onClick={() => void draftProposal('work_order.hold')}
              data-testid='scoped-chat-draft-hold'
            >
              {t`Draft hold proposal`}
            </Button>
            <Button
              size='xs'
              variant='light'
              color='teal'
              leftSection={<IconPlayerPlay size={14} />}
              onClick={() => void draftProposal('work_order.resume')}
              data-testid='scoped-chat-draft-resume'
            >
              {t`Draft resume proposal`}
            </Button>
          </Group>
          {proposalError && (
            <Alert color='red' p='xs'>
              <Text size='xs'>{proposalError}</Text>
            </Alert>
          )}
        </>
      )}
      {proposals.length > 0 && (
        <Stack gap='xs'>
          {proposals.map((proposal) => (
            <ProposalCard
              key={proposal.id}
              proposal={proposal}
              onChanged={() => void refreshProposals()}
            />
          ))}
        </Stack>
      )}
      <Text size='xs' c='dimmed'>
        {t`Chat drafts, humans act: nothing here changes safety state, and actions execute only after explicit confirmation.`}
      </Text>
    </Stack>
  );
}
