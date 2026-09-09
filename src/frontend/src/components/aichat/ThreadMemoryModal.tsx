/**
 * "What this chat remembers" (M2 PR 9; plan 8.6 items 3-4, GR-16).
 *
 * The ONLY place the summary body renders. Owner-only by construction: the
 * drawer shows the opening icon on owned, persisted rows alone, and the
 * server answers a grantee with 404, which paints the "not available"
 * state below — never a server error body. Every item is labelled as a
 * model-written summary, not verified data, and the two verbs ("This is
 * wrong", "Forget") are supersession/exclusion writes through the
 * corrections endpoint — never a prose edit.
 */

import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Button,
  Group,
  List,
  Loader,
  Modal,
  Stack,
  Text
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';

import type {
  ThreadMemoryItem,
  ThreadMemoryResponse
} from '@lib/types/AimmsWire.generated';
import {
  type ThreadMemoryCorrectionAction,
  useThreadMemory
} from '../../hooks/useThreadMemory';

type ListKey =
  | 'open_questions'
  | 'pending_proposals'
  | 'machine_facts'
  | 'corrections';

function sectionTitle(key: ListKey): string {
  switch (key) {
    case 'open_questions':
      return t`Open questions`;
    case 'pending_proposals':
      return t`Pending proposals`;
    case 'machine_facts':
      return t`Machine facts`;
    case 'corrections':
      return t`Corrections`;
  }
}

function lifecycleLabel(lifecycle: string): string {
  switch (lifecycle) {
    case 'superseded':
      return t`superseded`;
    case 'expired':
      return t`expired`;
    case 'forgotten':
      return t`forgotten`;
    case 'removed':
      return t`removed`;
    default:
      // Closed vocabulary server side; an unknown code is still a code.
      return lifecycle;
  }
}

function MemoryItemRow({
  item,
  busy,
  onCorrect
}: Readonly<{
  item: ThreadMemoryItem;
  busy: boolean;
  onCorrect: (itemId: string, action: ThreadMemoryCorrectionAction) => void;
}>) {
  const active = item.lifecycle === 'active';
  const flagged =
    Array.isArray(item.directive_flags) && item.directive_flags.length > 0;

  return (
    <Group
      gap='xs'
      wrap='nowrap'
      align='flex-start'
      data-testid={`memory-item-${item.id}`}
      data-lifecycle={item.lifecycle}
    >
      <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
        <Text
          size='sm'
          c={active ? undefined : 'dimmed'}
          td={active ? undefined : 'line-through'}
          style={{ wordBreak: 'break-word' }}
        >
          {item.text}
        </Text>
        <Group gap={4}>
          {!active && (
            <Badge size='xs' variant='light' color='gray'>
              {lifecycleLabel(item.lifecycle)}
            </Badge>
          )}
          {flagged && (
            <Badge size='xs' variant='light' color='orange'>
              {t`flagged`}
            </Badge>
          )}
        </Group>
      </Stack>
      {active && (
        <Group gap={4} wrap='nowrap'>
          <Button
            size='compact-xs'
            variant='subtle'
            color='orange'
            aria-label={`memory-item-wrong-${item.id}`}
            disabled={busy}
            onClick={() => onCorrect(item.id, 'wrong')}
          >
            {t`This is wrong`}
          </Button>
          <Button
            size='compact-xs'
            variant='subtle'
            color='red'
            aria-label={`memory-item-forget-${item.id}`}
            disabled={busy}
            onClick={() => onCorrect(item.id, 'forget')}
          >
            {t`Forget`}
          </Button>
        </Group>
      )}
    </Group>
  );
}

function MemorySection({
  listKey,
  items,
  busyItemId,
  onCorrect
}: Readonly<{
  listKey: ListKey;
  items: ThreadMemoryItem[];
  busyItemId: string | null;
  onCorrect: (itemId: string, action: ThreadMemoryCorrectionAction) => void;
}>) {
  return (
    <Stack gap={4} data-testid={`memory-section-${listKey}`}>
      <Text size='sm' fw={600}>
        {sectionTitle(listKey)} ({items.length})
      </Text>
      {items.length === 0 ? (
        <Text size='xs' c='dimmed'>
          {t`None`}
        </Text>
      ) : (
        items.map((item) => (
          <MemoryItemRow
            key={item.id}
            item={item}
            busy={busyItemId !== null}
            onCorrect={onCorrect}
          />
        ))
      )}
    </Stack>
  );
}

function MemoryBody({
  memory,
  busyItemId,
  onCorrect
}: Readonly<{
  memory: ThreadMemoryResponse;
  busyItemId: string | null;
  onCorrect: (itemId: string, action: ThreadMemoryCorrectionAction) => void;
}>) {
  const through = memory.through_sequence;
  const latest = memory.latest_sequence;
  const forgotten = memory.exclusions_count;
  const lists: ListKey[] = [
    'open_questions',
    'pending_proposals',
    'machine_facts',
    'corrections'
  ];

  return (
    <Stack gap='sm'>
      <Stack gap={2}>
        {memory.label && (
          <Text size='sm' fw={600} data-testid='thread-memory-label'>
            {memory.label}
          </Text>
        )}
        <Text size='xs' c='dimmed' data-testid='thread-memory-watermark'>
          {t`Through message ${through} of ${latest}`}
        </Text>
      </Stack>

      {lists.map((key) => (
        <MemorySection
          key={key}
          listKey={key}
          items={memory[key] ?? []}
          busyItemId={busyItemId}
          onCorrect={onCorrect}
        />
      ))}

      {memory.citation_keys.length > 0 && (
        <Stack gap={4} data-testid='thread-memory-citations'>
          <Text size='sm' fw={600}>
            {t`Citations`} ({memory.citation_keys.length})
          </Text>
          <List size='xs' spacing={2}>
            {memory.citation_keys.map((key) => (
              <List.Item key={key}>{key}</List.Item>
            ))}
          </List>
        </Stack>
      )}

      {memory.narrative && (
        <Stack gap={4} data-testid='thread-memory-narrative'>
          <Text size='sm' fw={600}>
            {t`Narrative`}
          </Text>
          <Text size='sm' style={{ whiteSpace: 'pre-wrap' }}>
            {memory.narrative}
          </Text>
        </Stack>
      )}

      <Text size='xs' c='dimmed' data-testid='thread-memory-exclusions'>
        {t`${forgotten} forgotten`}
      </Text>
    </Stack>
  );
}

export function ThreadMemoryModal({
  threadId,
  host,
  opened,
  onClose
}: Readonly<{
  threadId: string | null;
  host: string;
  opened: boolean;
  onClose: () => void;
}>) {
  const { status, memory, notice, busyItemId, correct } = useThreadMemory(
    threadId,
    host,
    opened && threadId !== null
  );

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={t`What this chat remembers`}
      size='lg'
      zIndex={3000}
      aria-label='thread-memory-modal'
      data-testid='thread-memory-modal'
      closeButtonProps={{ 'aria-label': 'thread-memory-close' }}
    >
      <Stack gap='sm'>
        <Alert
          color='yellow'
          icon={<IconAlertTriangle size={16} />}
          title={t`Summary, not verified data`}
          data-testid='thread-memory-disclaimer'
        >
          <Text size='xs'>
            {t`These items were written by the assistant from this conversation. Nothing here has been checked against records. Mark an item wrong or forget it to correct what is carried forward.`}
          </Text>
        </Alert>

        {notice === 'conflict' && (
          <Alert color='orange' data-testid='thread-memory-notice'>
            {t`Memory changed, try again`}
          </Alert>
        )}
        {notice === 'failed' && (
          <Alert color='red' data-testid='thread-memory-notice'>
            {t`Could not update memory`}
          </Alert>
        )}

        {(status === 'idle' || status === 'loading') && !memory && (
          <Group justify='center' py='md'>
            <Loader size='sm' />
          </Group>
        )}
        {status === 'not_found' && (
          <Text size='sm' c='dimmed' data-testid='thread-memory-unavailable'>
            {t`Memory is not available for this conversation`}
          </Text>
        )}
        {status === 'error' && (
          <Text size='sm' c='dimmed' data-testid='thread-memory-error'>
            {t`Could not load memory`}
          </Text>
        )}
        {memory && (
          <MemoryBody
            memory={memory}
            busyItemId={busyItemId}
            onCorrect={(itemId, action) => void correct(itemId, action)}
          />
        )}
      </Stack>
    </Modal>
  );
}
