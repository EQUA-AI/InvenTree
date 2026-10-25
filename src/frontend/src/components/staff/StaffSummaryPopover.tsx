import type { StaffScope } from '@lib/types/Staff';
import { t } from '@lingui/core/macro';
import { Button, Loader, Popover, Stack, Text } from '@mantine/core';
import { useState } from 'react';
import { useStaffAccess, useStaffMember } from './useStaffDirectory';

export function StaffSummaryPopover({
  userId,
  label,
  scope
}: {
  userId: number | null;
  label?: string | null;
  scope: StaffScope;
}) {
  const [opened, setOpened] = useState(false);
  const access = useStaffAccess();
  const person = useStaffMember(scope, userId, opened);
  const fallback =
    label || (userId != null ? t`Staff #${userId}` : t`Unassigned`);
  if (!access.directory || userId == null)
    return <Text size='sm'>{fallback}</Text>;
  return (
    <Popover
      opened={opened}
      onChange={setOpened}
      trapFocus
      returnFocus
      withinPortal
    >
      <Popover.Target>
        <Button
          variant='subtle'
          size='compact-sm'
          style={{ whiteSpace: 'normal', height: 'auto', textAlign: 'left' }}
          aria-label={t`Staff details: ${fallback}`}
          aria-expanded={opened}
          aria-haspopup='dialog'
          onClick={() => setOpened((value) => !value)}
        >
          {fallback}
        </Button>
      </Popover.Target>
      <Popover.Dropdown aria-label={t`Staff details`}>
        {!access.directory ? null : person.isFetching ? (
          <Loader size='xs' />
        ) : !person.isError && person.data ? (
          <Stack gap={4}>
            <Text fw={600}>{person.data.display_name}</Text>
            <Text size='sm'>
              {person.data.job_title || t`Job title not provided`}
            </Text>
          </Stack>
        ) : (
          <Text component='output' size='sm'>
            {person.isError
              ? t`Staff details could not be loaded.`
              : t`Staff details are not available in this scope.`}
          </Text>
        )}
      </Popover.Dropdown>
    </Popover>
  );
}
