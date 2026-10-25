import { t } from '@lingui/core/macro';
import { Select, Stack, Text } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';

interface Staff {
  id: number;
  name: string;
  username: string;
}

export function WorkOrderAssigneeSelect({
  machineId,
  value,
  onChange,
  suggestedUsername,
  disabled
}: Readonly<{
  machineId: number | null;
  value: string | null;
  onChange: (value: string | null) => void;
  suggestedUsername: string | null;
  disabled: boolean;
}>) {
  const api = useApi();
  const generation = useUserState((state) => state.authGeneration);
  const suggestedApplied = useRef<string | null>(null);
  const [search, setSearch] = useState('');
  const [debouncedSearch] = useDebouncedValue(search, 250);
  const [selected, setSelected] = useState<Staff | null>(null);
  const staff = useQuery<Staff[]>({
    queryKey: ['work-intake-assignees', machineId, generation, debouncedSearch],
    enabled: machineId !== null,
    queryFn: async () =>
      (
        await api.get('/api/maintenance/work-packages/assignees/', {
          params: { machine_id: machineId, search: debouncedSearch }
        })
      ).data.results,
    retry: false,
    staleTime: 0
  });
  const suggested = useQuery<Staff[]>({
    queryKey: ['work-intake-owner', machineId, generation, suggestedUsername],
    enabled: machineId !== null && Boolean(suggestedUsername),
    queryFn: async () =>
      (
        await api.get('/api/maintenance/work-packages/assignees/', {
          params: { machine_id: machineId, search: suggestedUsername }
        })
      ).data.results,
    retry: false,
    staleTime: 0
  });
  const options = useMemo(() => {
    const rows = new Map(
      (staff.data ?? []).map((person) => [person.id, person])
    );
    if (selected && String(selected.id) === value)
      rows.set(selected.id, selected);
    return [...rows.values()].map((person) => ({
      value: String(person.id),
      label: `${person.name} · ${person.username}`
    }));
  }, [staff.data, selected, value]);
  useEffect(() => {
    if (
      !suggestedUsername ||
      !suggested.data ||
      disabled ||
      suggestedApplied.current === suggestedUsername
    )
      return;
    const candidates = suggested.data.filter(
      (person) => person.username === suggestedUsername
    );
    if (candidates.length === 1) {
      suggestedApplied.current = suggestedUsername;
      if (value) return;
      setSelected(candidates[0]);
      onChange(String(candidates[0].id));
    }
  }, [suggestedUsername, suggested.data, onChange, disabled, value]);
  return (
    <Stack gap={3} style={{ flex: 1, minWidth: 0 }}>
      <Select
        label={t`Assigned staff`}
        placeholder={
          staff.isPending
            ? t`Loading eligible staff…`
            : t`Select an active owner`
        }
        data={options}
        value={value}
        onChange={(next) => {
          suggestedApplied.current = suggestedUsername;
          setSelected(
            staff.data?.find((person) => String(person.id) === next) ?? null
          );
          onChange(next);
        }}
        searchable
        searchValue={search}
        onSearchChange={(next) => setSearch(next.slice(0, 100))}
        filter={({ options: rows }) => rows}
        clearable
        disabled={disabled || !machineId}
        nothingFoundMessage={t`No eligible staff found`}
        error={
          staff.isError
            ? t`Staff directory unavailable or assignment not permitted.`
            : undefined
        }
      />
      <Text size='xs'>{t`Assignment proposes a handover for acceptance. Qualifications and physical work authority remain separate.`}</Text>
      {suggestedUsername &&
        suggested.isSuccess &&
        !suggested.data?.some(
          (person) => person.username === suggestedUsername
        ) && (
          <Text
            size='xs'
            c='orange'
          >{t`The specified exercise owner is not available. Choose authorized staff; no identity was substituted.`}</Text>
        )}
    </Stack>
  );
}
