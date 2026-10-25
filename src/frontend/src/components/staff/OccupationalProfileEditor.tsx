import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import type { OccupationalProfile } from '@lib/types/Staff';
import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Loader,
  MultiSelect,
  Stack,
  TextInput
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useApi } from '../../contexts/ApiContext';
import { useUserState } from '../../states/UserState';
import { useStaffAccess, useStaffTrades } from './useStaffDirectory';

export function OccupationalProfileEditor({
  userId,
  onSaved
}: { userId: number; onSaved: () => void }) {
  const api = useApi();
  const client = useQueryClient();
  const access = useStaffAccess();
  const canEdit = useUserState(
    (state) =>
      state.isSuperuser() ||
      (state.isStaff() && state.user?.permissions?.user?.includes('change'))
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const profile = useQuery({
    queryKey: ['occupational-profile', ...access.cacheKey, userId],
    enabled: !!canEdit,
    retry: false,
    gcTime: 0,
    queryFn: async ({ signal }) =>
      (
        await api.get<OccupationalProfile>(
          apiUrl(ApiEndpoints.user_occupational_profile, userId),
          { signal }
        )
      ).data
  });
  const trades = useStaffTrades(!!canEdit);
  const catalogue = trades.data?.pages.flatMap((page) => page.results) ?? [];
  const form = useForm({
    initialValues: { position: '', trades: [] as string[] },
    validate: {
      position: (value) =>
        value.length > 255
          ? t`Job title must be 255 characters or fewer.`
          : null
    }
  });
  const setValues = form.setValues;
  useEffect(() => {
    if (profile.data)
      setValues({
        position: profile.data.position ?? '',
        trades: profile.data.trades.map(String)
      });
  }, [profile.data, setValues]);
  if (!canEdit) return null;
  if (profile.isLoading) return <Loader size='sm' />;
  if (profile.isError)
    return (
      <Alert color='red'>{t`Occupational profile could not be loaded.`}</Alert>
    );
  return (
    <form
      onSubmit={form.onSubmit(async (values) => {
        setSaving(true);
        setError('');
        try {
          await api.patch(
            apiUrl(ApiEndpoints.user_occupational_profile, userId),
            {
              position: values.position.trim() || null,
              trades: values.trades.map(Number)
            } satisfies OccupationalProfile
          );
          await Promise.all([
            client.invalidateQueries({ queryKey: ['staff-member'] }),
            client.invalidateQueries({ queryKey: ['staff-directory'] }),
            profile.refetch()
          ]);
          if (useUserState.getState().user?.pk === userId)
            await useUserState.getState().fetchUserState();
          onSaved();
        } catch {
          setError(
            t`Occupational profile was not saved. Check your permissions and try again.`
          );
        } finally {
          setSaving(false);
        }
      })}
    >
      <Stack gap='sm'>
        <TextInput
          label={t`Job title`}
          maxLength={255}
          disabled={saving}
          {...form.getInputProps('position')}
        />
        <MultiSelect
          label={t`Approved trades`}
          description={t`Administrator-managed assignment suitability, not verified licensing or certification.`}
          searchable
          disabled={saving || trades.isError}
          data={[
            ...catalogue.map((trade) => ({
              value: String(trade.pk),
              label: trade.name,
              disabled: !trade.active
            })),
            ...form.values.trades
              .filter(
                (id) => !catalogue.some((trade) => String(trade.pk) === id)
              )
              .map((id) => ({
                value: id,
                label: t`Trade #${id}`,
                disabled: true
              }))
          ]}
          {...form.getInputProps('trades')}
          error={trades.isError ? t`Trade catalogue unavailable` : undefined}
        />
        {trades.hasNextPage && (
          <Button
            size='xs'
            variant='subtle'
            onClick={() => trades.fetchNextPage()}
          >{t`Load more trades`}</Button>
        )}
        {trades.isError && (
          <Button
            variant='subtle'
            size='xs'
            onClick={() => trades.refetch()}
          >{t`Retry trade catalogue`}</Button>
        )}
        {error && (
          <Alert role='alert' color='red'>
            {error}
          </Alert>
        )}
        <Group justify='flex-end'>
          <Button
            type='submit'
            loading={saving}
            disabled={!profile.data || trades.isError}
          >{t`Save occupational profile`}</Button>
        </Group>
      </Stack>
    </form>
  );
}
