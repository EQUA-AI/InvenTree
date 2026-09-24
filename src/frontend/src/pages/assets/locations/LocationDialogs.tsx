import { t } from '@lingui/core/macro';
import {
  Alert,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Switch,
  Text,
  TextInput,
  Textarea
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { useMediaQuery } from '@mantine/hooks';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useApi } from '../../../contexts/ApiContext';
import { LocationPicker } from './LocationPicker';
import {
  type LocatedMachine,
  type LocationContext,
  type LocationNode,
  locationApi,
  locationPath
} from './locationTypes';

function saveError(error: any) {
  if (error?.response?.status === 409)
    return t`This record changed. Close this dialog, refresh, and review before trying again.`;
  if (error?.response?.status === 403)
    return t`You no longer have permission to make this change.`;
  const detail = error?.response?.data;
  if (typeof detail?.detail === 'string') return detail.detail;
  if (detail && typeof detail === 'object')
    return Object.values(detail)
      .flat()
      .filter((v) => typeof v === 'string')
      .join(' ');
  return t`The change could not be saved. Your inputs have been kept.`;
}

export function LocationEditDialog({
  node,
  parent,
  context,
  onClose,
  onSaved
}: {
  node?: LocationNode;
  parent?: LocationNode;
  context: LocationContext;
  onClose: () => void;
  onSaved: (node: LocationNode) => void;
}) {
  const api = useApi();
  const queryClient = useQueryClient();
  const small = useMediaQuery('(max-width: 48em)');
  const form = useForm({
    initialValues: {
      name: node?.name ?? '',
      code: node?.code ?? '',
      kind: node?.kind ?? (parent ? 'area' : 'site'),
      client: String(
        node?.client ?? parent?.client ?? context.workspaces[0]?.pk ?? ''
      ),
      parent: node?.parent
        ? String(node.parent)
        : parent
          ? String(parent.pk)
          : null,
      timezone:
        node?.timezone ??
        (parent ? '' : Intl.DateTimeFormat().resolvedOptions().timeZone),
      description: node?.description ?? '',
      archived: node?.archived ?? false,
      reason: ''
    },
    validate: {
      name: (v) => (v.trim() ? null : t`Enter a name`),
      code: (v) =>
        /^[a-zA-Z0-9_-]+$/.test(v)
          ? null
          : t`Use letters, numbers, hyphens or underscores`,
      client: (v) => (v ? null : t`Choose a workspace`),
      reason: (v) => (v.trim() ? null : t`Enter a reason`)
    }
  });
  const mutation = useMutation({
    mutationFn: async (values: typeof form.values) => {
      const data = {
        ...values,
        client: Number(values.client),
        parent: values.parent ? Number(values.parent) : null,
        ...(node ? { expected_version: node.version } : {})
      };
      return (
        await (node
          ? api.patch(`${locationApi}${node.pk}/`, data)
          : api.post(locationApi, data))
      ).data as LocationNode;
    },
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ['asset-locations'] });
      onSaved(data);
    }
  });
  const kinds = [
    ['site', t`Site`],
    ['facility', t`Facility`],
    ['building', t`Building`],
    ['area', t`Area`],
    ['line', t`Production Line`],
    ['cell', t`Cell`],
    ['room', t`Room`],
    ['other', t`Other`]
  ].map(([value, label]) => ({ value, label }));
  const originalParent =
    parent ??
    (node?.parent
      ? { ...node, pk: node.parent, path: node.path.slice(0, -1) }
      : null);
  return (
    <Modal
      opened
      onClose={onClose}
      title={
        node ? t`Edit location` : parent ? t`Add sublocation` : t`Add location`
      }
      size='lg'
      fullScreen={small}
      closeOnClickOutside={!mutation.isPending}
      closeOnEscape={!mutation.isPending}
      withCloseButton={!mutation.isPending}
    >
      <form onSubmit={form.onSubmit((values) => mutation.mutate(values))}>
        <Stack>
          {mutation.isError && (
            <Alert color='red'>{saveError(mutation.error)}</Alert>
          )}
          {context.workspaces.length > 1 && (
            <Select
              label={t`Workspace`}
              data={context.workspaces.map((w) => ({
                value: String(w.pk),
                label: w.name
              }))}
              value={form.values.client}
              disabled={!!node || !!parent}
              onChange={(value) => {
                form.setFieldValue('client', value ?? '');
                form.setFieldValue('parent', null);
              }}
            />
          )}
          <TextInput
            label={t`Name`}
            required
            maxLength={255}
            {...form.getInputProps('name')}
          />
          <Group grow>
            <TextInput
              label={t`Code`}
              required
              maxLength={64}
              {...form.getInputProps('code')}
            />
            <Select
              label={t`Type`}
              data={kinds}
              allowDeselect={false}
              {...form.getInputProps('kind')}
            />
          </Group>
          <LocationPicker
            label={t`Parent location`}
            client={Number(form.values.client)}
            selected={originalParent}
            value={form.values.parent}
            onChange={(v) => form.setFieldValue('parent', v)}
          />
          <TextInput
            label={t`Timezone`}
            description={
              form.values.parent
                ? t`Leave blank to inherit the parent timezone.`
                : t`Required for a top-level location, for example Europe/London.`
            }
            {...form.getInputProps('timezone')}
          />
          {node &&
            (form.values.parent !==
              (node.parent ? String(node.parent) : null) ||
              form.values.timezone !== node.timezone) && (
              <Alert color='yellow'>{t`Changing ancestry or timezone affects future reporting. Earlier history and existing maintenance schedules are preserved.`}</Alert>
            )}
          <Textarea
            label={t`Description`}
            maxLength={10000}
            {...form.getInputProps('description')}
          />
          {node && (
            <Switch
              label={t`Archived`}
              description={t`Active machines and child locations must be moved or retired first.`}
              {...form.getInputProps('archived', { type: 'checkbox' })}
            />
          )}
          <Textarea
            label={t`Reason for change`}
            required
            maxLength={1000}
            {...form.getInputProps('reason')}
          />
          <Group justify='flex-end'>
            <Button
              variant='default'
              onClick={onClose}
              disabled={mutation.isPending}
            >{t`Cancel`}</Button>
            <Button
              type='submit'
              loading={mutation.isPending}
            >{t`Save location`}</Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}

export function MachineMoveDialog({
  machines,
  onClose,
  onSaved
}: { machines: LocatedMachine[]; onClose: () => void; onSaved: () => void }) {
  const api = useApi();
  const queryClient = useQueryClient();
  const small = useMediaQuery('(max-width: 48em)');
  const [key, setKey] = useState(() => crypto.randomUUID());
  const [review, setReview] = useState(false);
  const form = useForm({
    initialValues: {
      destination: machines[0]?.physical_location
        ? String(machines[0].physical_location.pk)
        : (null as string | null),
      reason: ''
    },
    validate: { reason: (v) => (v.trim() ? null : t`Enter a reason`) }
  });
  const mutation = useMutation({
    mutationFn: async () =>
      (
        await api.post(`${locationApi}transfers/`, {
          machines: machines.map((m) => ({
            machine_id: m.pk,
            expected_placement_version: m.placement_version
          })),
          destination_location_id: form.values.destination
            ? Number(form.values.destination)
            : null,
          reason: form.values.reason,
          idempotency_key: key
        })
      ).data,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['asset-locations'] }),
        queryClient.invalidateQueries({ queryKey: ['maintenance-metrics'] })
      ]);
      onSaved();
    }
  });
  const mixed = new Set(machines.map((m) => m.client)).size !== 1;
  return (
    <Modal
      opened
      onClose={onClose}
      title={t`Move machines`}
      size='lg'
      fullScreen={small}
      closeOnClickOutside={!mutation.isPending}
      closeOnEscape={!mutation.isPending}
      withCloseButton={!mutation.isPending}
    >
      <form
        onSubmit={form.onSubmit(() =>
          review ? mutation.mutate() : setReview(true)
        )}
      >
        <Stack>
          {mutation.isError && (
            <Alert color='red'>{saveError(mutation.error)}</Alert>
          )}
          {mixed && (
            <Alert color='red'>{t`Select machines from one workspace for each move.`}</Alert>
          )}
          <Text fw={600}>
            {t`Selected machines`}: {machines.length}
          </Text>
          <Stack gap={4}>
            {machines.map((m) => (
              <Text key={m.pk} size='sm'>
                {m.name} —{' '}
                {m.physical_location
                  ? locationPath(m.physical_location)
                  : t`Unassigned`}
              </Text>
            ))}
          </Stack>
          <LocationPicker
            label={t`Destination`}
            client={machines[0]?.client ?? undefined}
            selected={machines[0]?.physical_location}
            value={form.values.destination}
            disabled={review}
            onChange={(value) => {
              form.setFieldValue('destination', value);
              setKey(crypto.randomUUID());
            }}
          />
          <Textarea
            label={t`Reason for move`}
            required
            maxLength={1000}
            disabled={review}
            {...form.getInputProps('reason')}
          />
          {!form.values.destination && (
            <Alert color='yellow'>{t`These machines will become unassigned. Their previous placement history will be retained.`}</Alert>
          )}
          <Text
            size='sm'
            c='dimmed'
          >{t`The move takes effect now. Existing work-order instructions and planned service venues are not changed.`}</Text>
          {review && (
            <Alert color='blue'>{t`Review the selected machines and destination. Confirming moves the entire selection together.`}</Alert>
          )}
          <Group justify='flex-end'>
            <Button
              variant='default'
              disabled={mutation.isPending}
              onClick={
                review
                  ? () => {
                      setReview(false);
                      setKey(crypto.randomUUID());
                    }
                  : onClose
              }
            >
              {review ? t`Back` : t`Cancel`}
            </Button>
            <Button type='submit' disabled={mixed} loading={mutation.isPending}>
              {review ? t`Confirm move` : t`Review move`}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
