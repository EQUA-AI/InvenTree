import type { StaffMember, StaffScope } from '@lib/types/Staff';
import { t } from '@lingui/core/macro';
import { Alert, Button, Select, Stack, Text } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useEffect, useMemo, useState } from 'react';
import { StaffSummaryPopover } from './StaffSummaryPopover';
import { assignmentReview } from './staffAssignment';
import {
  useStaffAccess,
  useStaffCandidates,
  useStaffMember
} from './useStaffDirectory';

export function StaffPicker({
  scope,
  value,
  onChange,
  onStaffChange,
  requiredTrades = [],
  confirmed = false,
  disabled = false,
  label = t`Assignee`,
  placeholder = t`Unassigned`
}: {
  scope: StaffScope;
  value: number | null;
  onChange: (value: number | null) => void;
  onStaffChange?: (staff: StaffMember | undefined) => void;
  requiredTrades?: string[];
  confirmed?: boolean;
  disabled?: boolean;
  label?: string;
  placeholder?: string;
}) {
  const access = useStaffAccess();
  const [search, setSearch] = useState('');
  const [serverSearch, setServerSearch] = useState('');
  const [debouncedSearch] = useDebouncedValue(serverSearch, 250);
  const directory = useStaffCandidates(scope, debouncedSearch, !disabled);
  const selected = useStaffMember(scope, value, !disabled);
  const people = useMemo(() => {
    const byId = new Map<number, StaffMember>();
    if (directory.isError || selected.isError) return byId;
    for (const page of directory.data?.pages ?? []) {
      for (const person of page.results) byId.set(person.user_id, person);
    }
    if (selected.data) byId.set(selected.data.user_id, selected.data);
    return byId;
  }, [directory.data, selected.data, directory.isError, selected.isError]);
  const person =
    access.directory && value != null ? people.get(value) : undefined;
  useEffect(() => onStaffChange?.(person), [person, onStaffChange]);
  const review = assignmentReview(person, requiredTrades, confirmed);
  if (!access.directory) {
    return (
      <Text size='sm'>{t`Staff directory access is required to select personnel.`}</Text>
    );
  }
  return (
    <Stack gap='xs'>
      <Select
        label={label}
        placeholder={placeholder}
        clearable
        clearButtonProps={{
          'aria-label': t`Clear assignee`,
          'aria-hidden': false,
          tabIndex: 0,
          size: 'md'
        }}
        searchable
        disabled={disabled}
        searchValue={search}
        onSearchChange={setSearch}
        onInput={(event) =>
          setServerSearch(event.currentTarget.value.slice(0, 100))
        }
        onFocus={() => {
          if (value == null) setSearch(serverSearch);
        }}
        filter={({ options }) => options}
        scrollAreaProps={{
          onBottomReached: () => {
            if (directory.hasNextPage && !directory.isFetchingNextPage) {
              void directory.fetchNextPage();
            }
          }
        }}
        data={[
          ...Array.from(people.values()).map((staff) => ({
            value: String(staff.user_id),
            label: `${staff.display_name} (#${staff.user_id}) — ${staff.job_title || t`Job title not provided`}`
          })),
          ...(value != null && !people.has(value)
            ? [
                {
                  value: String(value),
                  label: t`Staff #${value} — details unavailable`,
                  disabled: true
                }
              ]
            : [])
        ]}
        value={value == null ? null : String(value)}
        onChange={(id) => {
          setServerSearch('');
          onChange(id == null ? null : Number(id));
        }}
        nothingFoundMessage={
          directory.isFetching
            ? t`Loading staff…`
            : t`No staff found in this scope`
        }
        error={
          directory.isError || selected.isError
            ? t`The staff directory could not be loaded. This is not an empty candidate list.`
            : undefined
        }
      />
      {directory.hasNextPage && (
        <Button
          variant='subtle'
          size='xs'
          loading={directory.isFetchingNextPage}
          onClick={() => directory.fetchNextPage()}
        >
          {t`Load more staff`}
        </Button>
      )}
      {(directory.isError || selected.isError) && (
        <Button
          variant='subtle'
          size='xs'
          onClick={() => {
            void directory.refetch();
            if (value != null) void selected.refetch();
          }}
        >{t`Retry staff lookup`}</Button>
      )}
      {value != null && (
        <StaffSummaryPopover
          scope={scope}
          userId={value}
          label={person?.display_name}
        />
      )}
      {value != null && !person && !selected.isFetching && (
        <Alert color='orange'>{t`The selected staff member could not be verified. Reload or select another person; their identity has not been changed.`}</Alert>
      )}
      {review === 'unknown' && (
        <Alert color='yellow'>{t`Trade requirements are unknown or unconfirmed. This assignment requires human review.`}</Alert>
      )}
      {review === 'mismatch' && (
        <Alert color='orange'>{t`This person does not cover every required trade. Assignment requires an authorized human override and a recorded reason.`}</Alert>
      )}
    </Stack>
  );
}
