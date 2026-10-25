import { t } from '@lingui/core/macro';
import { ScrollArea, Text, VisuallyHidden } from '@mantine/core';
import { type ReactNode, type Ref, useEffect, useRef, useState } from 'react';
import workspaceStyles from './AssistantWorkspace.module.css';

export function ChatTranscript({
  viewportRef,
  children,
  active,
  busy,
  threadId
}: {
  viewportRef: Ref<HTMLDivElement>;
  children: ReactNode;
  active: boolean;
  busy: boolean;
  threadId: string;
}) {
  const previous = useRef({ threadId, busy });
  const [announcement, setAnnouncement] = useState('');
  useEffect(() => {
    if (
      active &&
      previous.current.threadId === threadId &&
      previous.current.busy &&
      !busy
    )
      setAnnouncement(
        t`Response finished. Review the conversation for the answer or any errors.`
      );
    else if (busy || previous.current.threadId !== threadId)
      setAnnouncement('');
    previous.current = { threadId, busy };
  }, [active, busy, threadId]);
  return (
    <>
      <VisuallyHidden>
        <Text component='output' aria-live='polite' aria-atomic='true'>
          {active ? announcement : ''}
        </Text>
      </VisuallyHidden>
      <ScrollArea
        style={{ flex: 1, minHeight: 0 }}
        offsetScrollbars
        scrollbarSize={6}
        viewportRef={viewportRef}
        data-testid='ai-chat-messages'
        viewportProps={{
          tabIndex: 0,
          className: workspaceStyles.transcriptViewport,
          role: 'region',
          'aria-label': active
            ? t`Conversation transcript`
            : t`Assistant workspace`,
          'aria-busy': busy,
          ...{ 'data-testid': 'ai-chat-messages-viewport' }
        }}
      >
        {children}
      </ScrollArea>
    </>
  );
}
