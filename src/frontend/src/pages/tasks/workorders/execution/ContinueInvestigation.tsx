import { t } from '@lingui/core/macro';
import { Button, Stack, Text } from '@mantine/core';
import { IconMessageChatbot } from '@tabler/icons-react';
import { useLocation } from 'react-router-dom';

import type { WorkOrderOverview } from '@lib/types/WorkOrderOverview';

import { openGlobalAIChatAboutWork } from '../../../../states/AIChatState';
import { diagnoseReturnPath } from './investigationProjection';

/**
 * Continue the investigation in the one existing assistant.
 *
 * It opens the assistant focused on this exact Work at the lifecycle version
 * shown, returning to the panel it was launched from. The assistant's Work
 * focus carries a saved request and revision when supplied. Evidence is shared
 * only through an explicitly approved proposal in that same assistant.
 */
export function ContinueInvestigation({
  workOrder,
  panel = 'diagnose',
  request,
  label,
  caption = true,
  variant = 'filled'
}: Readonly<{
  workOrder: Pick<
    WorkOrderOverview,
    'id' | 'lifecycle_version' | 'reference' | 'title'
  >;
  panel?: string;
  request?: { id: number; version: number };
  label?: string;
  caption?: boolean;
  variant?: 'filled' | 'light' | 'default';
}>) {
  const location = useLocation();
  const usable =
    Number.isSafeInteger(workOrder.lifecycle_version) &&
    workOrder.lifecycle_version > 0;
  const returnPath =
    panel === 'diagnose'
      ? diagnoseReturnPath(location.pathname, location.search)
      : (() => {
          const params = new URLSearchParams(location.search);
          if (panel === 'work') params.delete('work_panel');
          else params.set('work_panel', panel);
          const query = params.toString();
          return query ? `${location.pathname}?${query}` : location.pathname;
        })();
  return (
    <Stack gap={4} align='flex-start'>
      <Button
        variant={variant}
        size='sm'
        leftSection={<IconMessageChatbot size={16} />}
        disabled={!usable}
        onClick={() =>
          openGlobalAIChatAboutWork({
            workOrderId: workOrder.id,
            lifecycleVersion: workOrder.lifecycle_version,
            ...(request
              ? { requestId: request.id, requestVersion: request.version }
              : {}),
            label: `${workOrder.reference ?? `WO-${workOrder.id}`} · ${workOrder.title}`,
            returnPath
          })
        }
      >
        {label ?? t`Continue investigation`}
      </Button>
      {caption && (
        <Text size='sm' maw={560}>
          {request
            ? t`Opens the existing assistant on this saved request. Review and approve the assistant’s proposal in chat to share typed evidence. Chat alone does not resolve the request.`
            : t`Opens the existing assistant on this Work. To share evidence for a saved request, use that request’s Discuss action. Name any other finding in your message.`}
        </Text>
      )}
    </Stack>
  );
}

export default ContinueInvestigation;
