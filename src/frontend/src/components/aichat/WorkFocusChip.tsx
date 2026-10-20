/**
 * P02/P33: the Work order, reference position or recorded evidence item a
 * question or its answer was about.
 *
 * The question shows it from the server's acknowledgement; the answer shows
 * it from the ids the server saved with the turn, so it survives a reload.
 * Work/request state is independent of component/evidence availability.
 * A reloaded answer also says how that Work stands now, because a saved
 * answer must not pass for current: "changed since this answer" when its
 * lifecycle, machine or access boundary moved, and no link at all when the
 * reader can no longer see it. The chip only navigates; the Work page checks
 * access again. A reference position is a reference-model position, never a
 * verified fitted part.
 */
import { t } from '@lingui/core/macro';
import { Anchor, Badge, Group, Tooltip } from '@mantine/core';
import { IconClipboardList } from '@tabler/icons-react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { displayFocus } from '@lib/types/CanonicalDisplay';
import { referencePartLabel } from '../../pages/assets/equipment/referenceParts';
import type { TwinPartId } from '../../pages/assets/equipment/twinScene.mjs';

import { useAssistantNavigation } from './useAssistantNavigation';
import { type ChatWorkFocus, focusReturnPath } from './workFocus';

export function WorkFocusChip({ focus }: Readonly<{ focus: ChatWorkFocus }>) {
  const onNavigate = useAssistantNavigation();
  const queries = useQueryClient();
  const canonical = displayFocus(focus.identity);
  const workOrderId = canonical.record.work_order_id;
  const workLabel = focus.label?.trim()
    ? focus.label.trim()
    : workOrderId !== null
      ? t`Work order ${workOrderId}`
      : t`Machine ${focus.identity.machine_id ?? '?'}`;
  const evidenceKind = focus.identity.evidence?.kind;
  const evidenceLabel =
    evidenceKind === 'closeout_reading'
      ? t`Closeout reading`
      : evidenceKind === 'media'
        ? t`Media item`
        : t`Attachment`;
  const label = [
    workLabel,
    focus.identity.request_id
      ? `${t`Request ${focus.identity.request_id}`} · v${focus.identity.request_version}`
      : null,
    focus.identity.component
      ? `${t`Reference position`}: ${referencePartLabel(focus.identity.component.position_id as TwinPartId)}${focus.identity.component.slot_code ? ` · ${focus.identity.component.slot_code}` : ''}`
      : null,
    focus.identity.evidence
      ? `${evidenceLabel} #${focus.identity.evidence.id}`
      : null
  ]
    .filter(Boolean)
    .join(' · ');
  const unavailable = focus.state === 'unavailable';
  const changedDetail =
    focus.identity.schema_version === 3 && workOrderId === null
      ? t`The machine context has changed since this answer. Check the reference records before relying on it.`
      : focus.identity.request_id
        ? t`This Work or investigation request has changed since this answer. Check the saved request before relying on it.`
        : t`This work order's status, machine or access has changed since this answer. Check the work order before relying on it.`;
  const workBadge = (
    <Badge
      size='sm'
      variant='light'
      color={unavailable ? 'gray' : 'teal'}
      leftSection={<IconClipboardList size={12} />}
      style={{ cursor: unavailable ? 'default' : 'pointer', maxWidth: '100%' }}
      styles={
        focus.identity.request_id ||
        focus.identity.component ||
        focus.identity.evidence
          ? {
              root: { height: 'auto' },
              label: { whiteSpace: 'normal', overflowWrap: 'anywhere' }
            }
          : undefined
      }
    >
      {label}
    </Badge>
  );
  return (
    <Group
      gap={6}
      mt={6}
      wrap='wrap'
      data-testid='ai-chat-message-work-focus'
      data-work-focus-state={focus.state ?? 'live'}
      data-source-record={canonical.source.record}
      data-source-id={canonical.source.id}
    >
      {unavailable ? (
        // The reader can no longer see this Work: name it, link nowhere.
        <span
          title={label}
          style={{ maxWidth: '100%', display: 'inline-flex' }}
        >
          {workBadge}
        </span>
      ) : (
        <Anchor
          component={Link}
          to={focusReturnPath(focus.identity, focus.returnPath)}
          onClick={(event) => {
            if (
              !event.defaultPrevented &&
              event.button === 0 &&
              !event.ctrlKey &&
              !event.metaKey &&
              !event.shiftKey &&
              !event.altKey &&
              focus.identity.request_id
            ) {
              void queries.invalidateQueries({
                queryKey: ['work-order', workOrderId]
              });
            }
            onNavigate(event);
          }}
          underline='never'
          aria-label={
            focus.identity.component && workOrderId === null
              ? t`Open reference position: ${label}`
              : t`Open work order: ${label}`
          }
          title={label}
          style={{ maxWidth: '100%' }}
        >
          {workBadge}
        </Anchor>
      )}
      {Object.entries(focus.references ?? {}).map(([key, state]) => (
        <Badge
          key={key}
          size='sm'
          variant='outline'
          color={state === 'changed' ? 'yellow' : 'gray'}
          styles={{ root: { height: 'auto' }, label: { whiteSpace: 'normal' } }}
        >
          {key === 'component' ? t`Reference position` : t`Evidence`}:{' '}
          {state === 'current'
            ? t`Current`
            : state === 'changed'
              ? t`Changed since this answer`
              : t`No longer available to you`}
        </Badge>
      ))}
      {focus.state === 'changed' && (
        <Tooltip
          label={changedDetail}
          multiline
          w={260}
          withArrow
          // Keyboard and touch readers need the explanation as much as a
          // pointer does; hover alone would hide it from them.
          events={{ hover: true, focus: true, touch: true }}
        >
          <Badge
            size='sm'
            variant='light'
            color='yellow'
            tabIndex={0}
            aria-label={`${t`Changed since this answer`}. ${changedDetail}`}
          >
            {t`Changed since this answer`}
          </Badge>
        </Tooltip>
      )}
      {unavailable && (
        <Badge size='sm' variant='outline' color='gray'>
          {t`No longer available to you`}
        </Badge>
      )}
    </Group>
  );
}
