import type { StaffMember } from '@lib/types/Staff';
import { t } from '@lingui/core/macro';
import { Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { StaffPicker } from '../../../components/staff/StaffPicker';
import { useStaffAccess } from '../../../components/staff/useStaffDirectory';
import { useApi } from '../../../contexts/ApiContext';

export function WorkOrderAssigneeSelect({
  machineId,
  value,
  onChange,
  onStaffChange,
  requiredTrades = [],
  confirmed = false,
  suggestedUsername,
  disabled
}: Readonly<{
  machineId: number | null;
  value: string | null;
  onChange: (value: string | null) => void;
  onStaffChange?: (staff: StaffMember | undefined) => void;
  requiredTrades?: string[];
  confirmed?: boolean;
  suggestedUsername: string | null;
  disabled: boolean;
}>) {
  const api = useApi();
  const access = useStaffAccess();
  const suggestedApplied = useRef<string | null>(null);
  const suggestionKey = `${machineId}:${suggestedUsername}`;
  const suggested = useQuery<{ id: number }[]>({
    queryKey: [
      'work-intake-owner',
      ...access.cacheKey,
      machineId,
      suggestedUsername
    ],
    enabled:
      access.directory &&
      access.assign &&
      machineId !== null &&
      Boolean(suggestedUsername) &&
      !disabled,
    queryFn: async ({ signal }) =>
      (
        await api.get('/api/maintenance/work-packages/assignees/', {
          signal,
          params: {
            machine_id: machineId,
            search: suggestedUsername,
            exact_username: true
          }
        })
      ).data.results,
    retry: false,
    staleTime: 0,
    gcTime: 0
  });
  useEffect(() => {
    if (
      !suggestedUsername ||
      !suggested.data ||
      disabled ||
      suggestedApplied.current === suggestionKey
    )
      return;
    const candidates = suggested.data;
    if (candidates.length === 1) {
      suggestedApplied.current = suggestionKey;
      if (!value) onChange(String(candidates[0].id));
    }
  }, [
    suggestedUsername,
    suggested.data,
    suggestionKey,
    onChange,
    disabled,
    value
  ]);
  return (
    <Stack gap={3} style={{ flex: 1, minWidth: 0 }}>
      {machineId !== null && (
        <StaffPicker
          scope={{ machineId }}
          value={value === null ? null : Number(value)}
          onChange={(next) => {
            suggestedApplied.current = suggestionKey;
            onChange(next === null ? null : String(next));
          }}
          onStaffChange={onStaffChange}
          requiredTrades={requiredTrades}
          confirmed={confirmed}
          disabled={disabled || !access.assign}
          label={t`Assigned staff`}
          placeholder={t`Select an active owner`}
        />
      )}
      <Text size='xs'>{t`Assignment proposes a handover for acceptance. Qualifications and physical work authority remain separate.`}</Text>
      {suggestedUsername &&
        suggested.isSuccess &&
        suggested.data?.length !== 1 && (
          <Text
            size='xs'
            c='orange'
          >{t`The specified exercise owner is not available. Choose authorized staff; no identity was substituted.`}</Text>
        )}
    </Stack>
  );
}
