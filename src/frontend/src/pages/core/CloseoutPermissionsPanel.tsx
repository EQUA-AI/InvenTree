import { t } from '@lingui/core/macro';
import {
  Alert,
  Badge,
  Group,
  Loader,
  Stack,
  Switch,
  Table,
  Text,
  Tooltip
} from '@mantine/core';
import { IconShieldLock } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiEndpoints } from '@lib/enums/ApiEndpoints';
import { apiUrl } from '@lib/functions/Api';
import { useApi } from '../../contexts/ApiContext';

interface CloseoutPermissionRow {
  codename: string;
  name: string;
  granted_direct: boolean;
  via_groups: string[];
  effective: boolean;
}

interface CloseoutPermissionState {
  user: number;
  username: string;
  is_superuser: boolean;
  permissions: CloseoutPermissionRow[];
}

/**
 * Staff surface for the closeout permission set (capture/review/verify/…).
 *
 * These are additive Django permissions outside the role table, so the role
 * editor cannot manage them. Toggles write the DIRECT user grant only;
 * group-conferred grants are shown but must be edited on the group.
 */
export function CloseoutPermissionsPanel({
  userId
}: Readonly<{ userId: number | undefined }>) {
  const api = useApi();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ['closeout-permissions', userId],
    enabled: !!userId,
    retry: false,
    queryFn: () =>
      api
        .get(apiUrl(ApiEndpoints.closeout_permission_detail, userId))
        .then((response) => response.data as CloseoutPermissionState)
  });

  if (!userId || query.isLoading) {
    return <Loader size='sm' />;
  }
  if (query.isError) {
    return (
      <Alert color='yellow' icon={<IconShieldLock size={16} />}>
        {t`Closeout permissions are visible to administrators only.`}
      </Alert>
    );
  }
  const state = query.data;
  if (!state) {
    return null;
  }

  const toggle = (row: CloseoutPermissionRow, granted: boolean) => {
    setBusy(row.codename);
    setError(null);
    api
      .post(apiUrl(ApiEndpoints.closeout_permission_detail, userId), {
        codename: row.codename,
        granted
      })
      .then(() => query.refetch())
      .catch((err) => {
        setError(
          err?.response?.data?.detail || t`Failed to update the permission`
        );
      })
      .finally(() => setBusy(null));
  };

  return (
    <Stack gap='sm'>
      <Alert color='blue' variant='light' icon={<IconShieldLock size={16} />}>
        {t`Closeout duties are separate, additive permissions - they are not part of any role. Toggles below change this user's direct grants; grants inherited from a group are managed on the group.`}
      </Alert>
      {state.is_superuser && (
        <Alert color='orange' variant='light'>
          {t`This user is a superuser and holds every permission implicitly.`}
        </Alert>
      )}
      {error && (
        <Text size='sm' c='red'>
          {error}
        </Text>
      )}
      <Table data-testid='closeout-permissions-table'>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t`Permission`}</Table.Th>
            <Table.Th>{t`Effective`}</Table.Th>
            <Table.Th>{t`Direct grant`}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {state.permissions.map((row) => (
            <Table.Tr
              key={row.codename}
              data-testid={`closeout-perm-${row.codename}`}
            >
              <Table.Td>
                <Stack gap={0}>
                  <Text size='sm'>{row.name}</Text>
                  <Text size='xs' c='dimmed'>
                    {row.codename}
                  </Text>
                </Stack>
              </Table.Td>
              <Table.Td>
                <Group gap='xs'>
                  {row.effective ? (
                    <Badge color='green' variant='light'>
                      {t`Granted`}
                    </Badge>
                  ) : (
                    <Badge color='gray' variant='light'>
                      {t`Not granted`}
                    </Badge>
                  )}
                  {row.via_groups.map((group) => (
                    <Tooltip
                      key={group}
                      label={t`Inherited from group ${group}; edit it on the group.`}
                    >
                      <Badge color='blue' variant='outline'>
                        {group}
                      </Badge>
                    </Tooltip>
                  ))}
                </Group>
              </Table.Td>
              <Table.Td>
                <Switch
                  checked={row.granted_direct}
                  disabled={busy !== null}
                  onChange={(event) => toggle(row, event.currentTarget.checked)}
                  aria-label={t`Direct grant: ${row.codename}`}
                />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}

export default CloseoutPermissionsPanel;
