/**
 * Drop-in "Ask AIMMS" entry point for record detail surfaces.
 *
 * Opens a drawer hosting the scoped chat panel pinned to one record. The
 * button renders nothing when scoped chat is disabled server-side: the
 * context resolver fails closed and the panel reports unavailability.
 */

import { t } from '@lingui/core/macro';
import { Button, Drawer } from '@mantine/core';
import { IconMessageChatbot } from '@tabler/icons-react';
import { useState } from 'react';

import { ScopedChatPanel } from './ScopedChatPanel';

export function ScopedChatButton({
  contextType,
  objectId,
  label
}: Readonly<{
  contextType: string;
  objectId: string | number;
  label?: string;
}>) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        size='xs'
        variant='light'
        leftSection={<IconMessageChatbot size={14} />}
        onClick={() => setOpen(true)}
        data-testid='scoped-chat-button'
        aria-haspopup='dialog'
      >
        {label ?? t`Ask AIMMS`}
      </Button>
      <Drawer
        opened={open}
        onClose={() => setOpen(false)}
        position='right'
        size='md'
        title={t`Ask AIMMS about this record`}
        aria-label={t`Scoped chat`}
      >
        {open && (
          <ScopedChatPanel contextType={contextType} objectId={objectId} />
        )}
      </Drawer>
    </>
  );
}
