import { t } from '@lingui/core/macro';
import { Select } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useApi } from '../../../contexts/ApiContext';
import { useUserState } from '../../../states/UserState';
import {
  type LocationNode,
  type PageResult,
  locationApi,
  locationPath
} from './locationTypes';

/** Server-backed path search: selecting a parent is as valid as selecting a leaf. */
export function LocationPicker({
  value,
  onChange,
  client,
  selected,
  label,
  error,
  disabled
}: {
  value: string | null;
  onChange: (value: string | null) => void;
  client?: number;
  selected?: LocationNode | null;
  label: string;
  error?: React.ReactNode;
  disabled?: boolean;
}) {
  const api = useApi();
  const identity = useUserState((s) => s.authGeneration);
  const [search, setSearch] = useState('');
  const [choice, setChoice] = useState<{
    value: string;
    label: string;
    client?: number;
  } | null>(null);
  const [debounced] = useDebouncedValue(search, 200);
  const query = useQuery<PageResult<LocationNode>>({
    queryKey: ['asset-locations', identity, 'picker', client, debounced],
    queryFn: async ({ signal }) =>
      (
        await api.get(locationApi, {
          signal,
          params: { client, search: debounced, active_only: true, limit: 25 }
        })
      ).data,
    enabled: !!client && !disabled
  });
  const options = new Map(
    (query.isError ? [] : (query.data?.results ?? [])).map((node) => [
      String(node.pk),
      { value: String(node.pk), label: locationPath(node) }
    ])
  );
  if (!query.isError && selected && !options.has(String(selected.pk)))
    options.set(String(selected.pk), {
      value: String(selected.pk),
      label: locationPath(selected)
    });
  if (!query.isError && choice?.value === value && choice.client === client)
    options.set(choice.value, choice);
  return (
    <Select
      label={label}
      description={t`Search by name or code. Any active level can hold machines.`}
      value={value}
      onChange={(next, option) => {
        setChoice(
          option ? { value: option.value, label: option.label, client } : null
        );
        onChange(next);
      }}
      searchable
      clearable
      searchValue={search}
      onSearchChange={setSearch}
      data={[...options.values()]}
      filter={({ options }) => options}
      nothingFoundMessage={
        query.isError
          ? t`Locations could not be loaded`
          : query.isFetching
            ? t`Loading locations…`
            : t`No matching locations`
      }
      error={
        error ||
        (query.isError
          ? t`Locations could not be loaded. Try again.`
          : undefined)
      }
      disabled={disabled || !client}
    />
  );
}
