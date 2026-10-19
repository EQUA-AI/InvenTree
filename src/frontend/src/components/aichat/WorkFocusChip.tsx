/**
 * P02/P33: the Work order a question or its answer was about.
 *
 * The question shows it from the server's acknowledgement; the answer shows
 * it from the ids the server saved with the turn, so it survives a reload.
 * A reloaded answer also says how that Work stands now, because a saved
 * answer must not pass for current: "changed since this answer" when its
 * lifecycle, machine or access boundary moved, and no link at all when the
 * reader can no longer see it. The chip only navigates; the Work page checks
 * access again.
 */
import { t } from '@lingui/core/macro';
import { Anchor, Badge, Group, Tooltip } from '@mantine/core';
import { IconClipboardList } from '@tabler/icons-react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { useAssistantNavigation } from './useAssistantNavigation';
import { type ChatWorkFocus, workFocusReturnPath } from './workFocus';

export function WorkFocusChip({ focus }: Readonly<{ focus: ChatWorkFocus }>) {
  const onNavigate = useAssistantNavigation();
  const queries = useQueryClient();
  const workOrderId = focus.identity.work_order_id;
  const workLabel = focus.label?.trim()
    ? focus.label.trim()
    : t`Work order ${workOrderId}`;
  const label = focus.identity.request_id
    ? `${workLabel} · ${t`Request ${focus.identity.request_id}`} · v${focus.identity.request_version}`
    : workLabel;
  const unavailable = focus.state === 'unavailable';
  const changedDetail = focus.identity.request_id
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
        focus.identity.request_id
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
          to={workFocusReturnPath(
            workOrderId,
            focus.returnPath,
            focus.identity.request_id
          )}
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
          aria-label={t`Open work order: ${label}`}
          title={label}
          style={{ maxWidth: '100%' }}
        >
          {workBadge}
        </Anchor>
      )}
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
