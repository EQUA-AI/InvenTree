import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Modal,
  NativeSelect,
  Stack,
  Text,
  TextInput,
  Title
} from '@mantine/core';
import type { AxiosResponse } from 'axios';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import PageTitle from '../../components/nav/PageTitle';
import { useApi } from '../../contexts/ApiContext';
import { useAIChatState } from '../../states/AIChatState';
import { useLocalState } from '../../states/LocalState';
import { useUserState } from '../../states/UserState';
import { ExcludedConversations } from './ExcludedConversations';

type Fact = {
  id: string;
  version: number;
  text: string;
  text_lang: string;
  canonical_unit: string;
  memory_type: string;
  topics: string[];
  state: 'active' | 'proposed';
  origin: string;
  verification: string;
  shield_state: string;
  source_available: boolean;
  last_verified_at: string | null;
};
type Page = { results: Fact[]; next_cursor: string | null };
type Status = {
  notice_version: string;
  notice_text: string | null;
  notice_available: boolean;
  acknowledged: boolean;
  opted_out: boolean;
  extraction_enabled: boolean;
  recall_enabled: boolean;
  restore_hold: boolean;
  can_write: boolean;
  cleanup_pending: boolean;
};
type Proposal = {
  id: string;
  preview_hash: string;
  action_type: string;
  preview: {
    text?: string;
    text_lang?: string;
    memory_type?: string;
    topics?: string[];
    shield_state?: string;
    untrusted?: boolean;
    confirm_phrase?: string;
    warning?: string;
    count?: number;
    supersedes?: string | null;
    revives?: string | null;
  };
  receipt?: { status?: string };
};
const TYPES = [
  'equipment_fact',
  'procedure_note',
  'site_convention',
  'schedule',
  'open_issue',
  'contact_role',
  'user_preference'
];
const TOPICS = [
  'electrical',
  'hydraulic',
  'pneumatic',
  'mechanical',
  'thermal',
  'chemical',
  'gravity',
  'controls',
  'safety',
  'parts',
  'planning',
  'documentation'
];
const ROOT = '/api/aichat/memory/';

export default function MemoryPage() {
  const owner = useUserState((s) => s.user?.pk);
  const authed = useUserState((s) => s.is_authed);
  const generation = useAIChatState((s) => s.sessionGeneration);
  const host = useLocalState((s) => s.getHost());
  if (!authed || owner === undefined) return null;
  // A changed identity, host or entitlement destroys every in-memory response.
  return <MemoryContents key={`${host}:${owner}:${generation}`} />;
}

function MemoryContents() {
  const api = useApi();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedProposal = searchParams.get('proposal');
  const clearRequestedProposal = () => {
    if (!requestedProposal) return;
    const remaining = new URLSearchParams(searchParams);
    remaining.delete('proposal');
    setSearchParams(remaining, { replace: true });
  };
  const [status, setStatus] = useState<Status | null>(null);
  const [page, setPage] = useState<Page>({ results: [], next_cursor: null });
  const [state, setState] = useState('active');
  const [memoryType, setMemoryType] = useState('');
  const [topic, setTopic] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [message, setMessage] = useState('');
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [phrase, setPhrase] = useState('');
  const [reviewed, setReviewed] = useState(false);
  const [tagsFact, setTagsFact] = useState<Fact | null>(null);
  const [tags, setTags] = useState<string[]>([]);
  const alive = useRef(false);
  const inFlight = useRef(false);
  const ticket = useRef(0);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    alive.current = true;
    controller.current = new AbortController();
    return () => {
      alive.current = false;
      controller.current?.abort();
      ticket.current += 1;
    };
  }, []);

  const refresh = useCallback(
    async (cursor = '') => {
      const current = ++ticket.current;
      setError(false);
      // Do not retain old content after a failed reauthorization or filter change.
      setPage({ results: [], next_cursor: null });
      try {
        const [settings, facts] = await Promise.all([
          api.get<Status>(`${ROOT}settings/`, {
            signal: controller.current?.signal
          }),
          api.get<Page>(`${ROOT}facts/`, {
            params: { state, memory_type: memoryType, topic, cursor },
            signal: controller.current?.signal
          })
        ]);
        if (!alive.current || ticket.current !== current) return;
        setStatus(settings.data);
        setPage(facts.data);
      } catch {
        if (alive.current && ticket.current === current) {
          setStatus(null);
          setError(true);
        }
      }
    },
    [api, state, memoryType, topic]
  );

  useEffect(() => {
    setProposal(null);
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!requestedProposal) return;
    const abort = new AbortController();
    setProposal(null);
    setReviewed(false);
    setPhrase('');
    if (!/^[a-f0-9-]{36}$/i.test(requestedProposal)) {
      setError(true);
      return () => abort.abort();
    }
    void api
      .get<Proposal>(
        `${ROOT}proposals/${encodeURIComponent(requestedProposal)}/decision/`,
        {
          signal: abort.signal
        }
      )
      .then((response) => {
        if (!abort.signal.aborted && alive.current && !inFlight.current)
          setProposal(response.data);
      })
      .catch(() => {
        if (!abort.signal.aborted && alive.current) {
          setProposal(null);
          setError(true);
        }
      });
    return () => abort.abort();
  }, [api, requestedProposal]);

  const act = async (work: () => Promise<void>) => {
    if (inFlight.current) return;
    ticket.current += 1;
    inFlight.current = true;
    setBusy(true);
    setError(false);
    setMessage('');
    try {
      await work();
    } catch {
      if (alive.current) {
        setError(true);
        setProposal(null);
        setPage({ results: [], next_cursor: null });
      }
    } finally {
      inFlight.current = false;
      if (alive.current) setBusy(false);
    }
  };

  const prepare = async (action: string, fact?: Fact, topics?: string[]) => {
    clearRequestedProposal();
    setProposal(null);
    const result = await api.post<Proposal>(
      `${ROOT}proposals/`,
      {
        action_type: action,
        idempotency_key: crypto.randomUUID(),
        ...(fact
          ? { memory_fact_id: fact.id, expected_version: fact.version }
          : {}),
        ...(topics ? { topics } : {})
      },
      { signal: controller.current?.signal }
    );
    if (!alive.current) return;
    setProposal(result.data);
    setPhrase('');
    setReviewed(false);
    setTagsFact(null);
  };

  const decide = async (decision: 'confirm' | 'reject') => {
    if (!proposal) return;
    const result = await api.post<Proposal>(
      `${ROOT}proposals/${proposal.id}/decision/`,
      {
        decision,
        expected_preview_hash: proposal.preview_hash,
        confirm_phrase: phrase
      },
      { signal: controller.current?.signal }
    );
    if (!alive.current) return;
    setProposal(null);
    clearRequestedProposal();
    setMessage(
      result.data.receipt?.status === 'purge_incomplete'
        ? t`Deletion is still processing. Refresh to check its status.`
        : decision === 'reject'
          ? t`Suggestion rejected.`
          : t`Memory action completed.`
    );
    await refresh();
  };

  const exportFacts = async () => {
    const results: Fact[] = [];
    for (const selection of ['active', 'proposed']) {
      let cursor: string | null = '';
      let pages = 0;
      do {
        if (++pages > 100) throw new Error('export_limit');
        const response: AxiosResponse<Page> = await api.get<Page>(
          `${ROOT}export/`,
          {
            params: { state: selection, cursor },
            signal: controller.current?.signal
          }
        );
        if (!alive.current) return;
        results.push(...response.data.results);
        cursor = response.data.next_cursor;
      } while (cursor);
    }
    if (!alive.current) return;
    const blob = new Blob(
      [
        JSON.stringify(
          { exported_at: new Date().toISOString(), facts: results },
          null,
          2
        )
      ],
      { type: 'application/json' }
    );
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'aimms-memories.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMessage(
      t`Memory export downloaded. It contains the records available to you during this export.`
    );
  };

  return (
    <Stack p='md' data-testid='durable-memory-page'>
      <PageTitle title={t`What AIMMS remembers`} />
      <Text>{t`Review memories used across your conversations. Suggestions need individual review. Conversation summaries are managed inside each chat.`}</Text>
      {error && (
        <Alert color='red'>{t`Memory could not be loaded or changed. Your access or the record may have changed. Refresh and review again.`}</Alert>
      )}
      {message && <Alert>{message}</Alert>}
      {!status && !error && <Loader aria-label={t`Loading memory`} />}
      {status && (
        <Card withBorder>
          <Stack gap='sm'>
            <Group>
              <Badge>
                {status.extraction_enabled
                  ? t`Learning available`
                  : t`Learning unavailable`}
              </Badge>
              <Badge>
                {status.recall_enabled
                  ? t`Recall available`
                  : t`Recall unavailable`}
              </Badge>
            </Group>
            <Text size='sm'>{t`Client enrollment, your notice acknowledgement and each conversation's settings determine whether learning is eligible. Shared conversations are excluded.`}</Text>
            {status.cleanup_pending && (
              <Alert color='orange'>{t`Memory deletion is still processing. Refresh to check completion.`}</Alert>
            )}
            {status.restore_hold && (
              <Alert>{t`Memory is temporarily unavailable while data is being restored.`}</Alert>
            )}
            {status.notice_text && <Text size='sm'>{status.notice_text}</Text>}
            {!status.acknowledged && status.notice_available && (
              <Button
                disabled={busy || status.restore_hold}
                onClick={() =>
                  void act(async () => {
                    await api.post(
                      `${ROOT}notice/`,
                      { notice_version: status.notice_version },
                      { signal: controller.current?.signal }
                    );
                    if (alive.current) await refresh();
                  })
                }
              >{t`I have read and acknowledge this memory notice`}</Button>
            )}
            <Button
              color={status.opted_out ? undefined : 'red'}
              variant='light'
              disabled={busy || status.restore_hold}
              onClick={() =>
                void act(async () => {
                  await api.put(
                    `${ROOT}opt-out/`,
                    { opted_out: !status.opted_out },
                    { signal: controller.current?.signal }
                  );
                  if (alive.current) {
                    setProposal(null);
                    clearRequestedProposal();
                    await refresh();
                  }
                })
              }
            >
              {status.opted_out
                ? t`Allow eligible future memories`
                : t`Opt out and remove my memories`}
            </Button>
            <Text size='xs'>{t`Opting out removes learned memories and clears summaries. Original chat messages remain. Allowing memory again does not restore forgotten memories and requires a fresh notice acknowledgement.`}</Text>
          </Stack>
        </Card>
      )}
      <ExcludedConversations
        key={`${status?.opted_out}:${status?.acknowledged}`}
      />
      <Group>
        <NativeSelect
          label={t`Show`}
          value={state}
          disabled={busy}
          onChange={(e) => setState(e.currentTarget.value)}
          data={[
            { value: 'active', label: t`Confirmed memories` },
            { value: 'proposed', label: t`Suggestions` }
          ]}
        />
        <NativeSelect
          label={t`Type`}
          value={memoryType}
          disabled={busy}
          onChange={(e) => setMemoryType(e.currentTarget.value)}
          data={[
            { value: '', label: t`All types` },
            ...TYPES.map((value) => ({
              value,
              label: value.replaceAll('_', ' ')
            }))
          ]}
        />
        <NativeSelect
          label={t`Topic`}
          value={topic}
          disabled={busy}
          onChange={(e) => setTopic(e.currentTarget.value)}
          data={[{ value: '', label: t`All topics` }, ...TOPICS]}
        />
        <Button
          variant='default'
          disabled={busy}
          onClick={() => void act(() => refresh())}
        >{t`Refresh`}</Button>
        <Button
          variant='default'
          disabled={busy || !status}
          onClick={() => void act(exportFacts)}
        >{t`Export my memories`}</Button>
        {status?.can_write && (
          <Button
            color='red'
            disabled={busy || status.restore_hold}
            onClick={() => void act(() => prepare('memory.forget_all'))}
          >{t`Forget all memories`}</Button>
        )}
      </Group>
      {status && page.results.length === 0 && (
        <Text c='dimmed'>{t`No accessible memories match this page. Continue to the next page if one is available.`}</Text>
      )}
      {page.results.map((fact) => (
        <Card withBorder key={fact.id}>
          <Stack gap='xs'>
            <Text style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
              {fact.text}
            </Text>
            <Group gap='xs'>
              <Badge>{fact.verification.replaceAll('_', ' ')}</Badge>
              <Badge
                color={fact.shield_state === 'flagged' ? 'orange' : 'gray'}
              >
                {fact.shield_state}
              </Badge>
              <Text size='xs'>
                {fact.text_lang}
                {fact.canonical_unit ? ` · ${fact.canonical_unit}` : ''}
              </Text>
            </Group>
            <Text size='xs'>
              {fact.memory_type.replaceAll('_', ' ')} · {fact.topics.join(', ')}
            </Text>
            <Text size='xs'>
              {fact.origin.replaceAll('_', ' ')}
              {fact.last_verified_at
                ? ` · ${new Date(fact.last_verified_at).toLocaleString()}`
                : ''}
            </Text>
            <Text size='xs' c='dimmed'>
              {fact.source_available
                ? t`Source conversation retained`
                : t`Source unavailable`}
            </Text>
            {fact.verification === 'inferred' && (
              <Text size='sm'>{t`This is an unconfirmed suggestion, not verified operational data.`}</Text>
            )}
            {status?.can_write && (
              <Group>
                {fact.state === 'proposed' && (
                  <Button
                    disabled={
                      busy ||
                      !status.extraction_enabled ||
                      !['clear', 'flagged'].includes(fact.shield_state)
                    }
                    onClick={() =>
                      void act(() => prepare('memory.remember', fact))
                    }
                  >{t`Review suggestion`}</Button>
                )}
                {fact.state === 'active' && (
                  <Button
                    variant='default'
                    disabled={busy || !status.extraction_enabled}
                    onClick={() => {
                      setTagsFact(fact);
                      setTags(fact.topics);
                    }}
                  >{t`Edit topics`}</Button>
                )}
                <Button
                  color='red'
                  variant='subtle'
                  disabled={busy}
                  onClick={() => void act(() => prepare('memory.forget', fact))}
                >{t`Forget`}</Button>
              </Group>
            )}
          </Stack>
        </Card>
      ))}
      {page.next_cursor && (
        <Button
          disabled={busy}
          onClick={() => void act(() => refresh(page.next_cursor ?? ''))}
        >{t`Next page`}</Button>
      )}
      <Text
        size='sm'
        c='dimmed'
      >{t`To correct a claim, explain the correction in an eligible conversation, then review the new suggestion. Operational claims must be checked against their source records.`}</Text>
      <Modal
        opened={tagsFact !== null}
        onClose={() => {
          if (!busy) setTagsFact(null);
        }}
        title={t`Edit memory topics`}
      >
        <Stack>
          <Checkbox.Group value={tags} onChange={setTags}>
            <Stack>
              {TOPICS.map((value) => (
                <Checkbox
                  key={value}
                  value={value}
                  label={value}
                  disabled={!tags.includes(value) && tags.length >= 3}
                />
              ))}
            </Stack>
          </Checkbox.Group>
          <Button
            disabled={busy}
            onClick={() => {
              if (tagsFact)
                void act(() => prepare('memory.update', tagsFact, tags));
            }}
          >{t`Review change`}</Button>
        </Stack>
      </Modal>
      <Modal
        opened={proposal !== null}
        onClose={() => {
          if (!busy) {
            setProposal(null);
            clearRequestedProposal();
          }
        }}
        title={t`Review memory action`}
      >
        {proposal && (
          <Stack>
            <Title order={4}>
              {proposal.action_type.replace('memory.', '').replaceAll('_', ' ')}
            </Title>
            {proposal.preview.text && (
              <Text
                style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
              >
                {proposal.preview.text}
              </Text>
            )}
            {proposal.preview.topics && (
              <Text>{proposal.preview.topics.join(', ')}</Text>
            )}
            {proposal.preview.shield_state && (
              <Text>
                {t`Screening`}: {proposal.preview.shield_state}
              </Text>
            )}
            {proposal.preview.untrusted && (
              <Alert color='orange'>{t`The source is untrusted text. Confirming a preference does not authorize actions.`}</Alert>
            )}
            {proposal.preview.supersedes && (
              <Alert>{t`This replaces an existing memory in the same category.`}</Alert>
            )}
            {proposal.preview.revives && (
              <Alert color='orange'>{t`This explicitly creates a new memory after an earlier deletion.`}</Alert>
            )}
            {proposal.preview.warning && (
              <Text>{proposal.preview.warning}</Text>
            )}
            {proposal.preview.count !== undefined && (
              <Text>
                {t`Selected memories`}: {proposal.preview.count}
              </Text>
            )}
            {proposal.preview.confirm_phrase && (
              <TextInput
                label={t`Type the confirmation phrase`}
                description={proposal.preview.confirm_phrase}
                value={phrase}
                onChange={(e) => setPhrase(e.currentTarget.value)}
              />
            )}
            <Checkbox
              checked={reviewed}
              onChange={(e) => setReviewed(e.currentTarget.checked)}
              label={t`I have reviewed this exact action.`}
            />
            <Group>
              <Button
                disabled={
                  busy ||
                  !reviewed ||
                  (!!proposal.preview.confirm_phrase &&
                    phrase !== proposal.preview.confirm_phrase)
                }
                onClick={() => void act(() => decide('confirm'))}
              >{t`Confirm`}</Button>
              {proposal.action_type === 'memory.remember' && (
                <Button
                  variant='default'
                  disabled={busy}
                  onClick={() => void act(() => decide('reject'))}
                >{t`Reject suggestion`}</Button>
              )}
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
