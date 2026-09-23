import { t } from '@lingui/core/macro';
import { Alert, Button, Group, Loader, Stack, Text } from '@mantine/core';
import { useLocation, useNavigate } from 'react-router-dom';
import { checkLoginState, doLogout } from '../../functions/auth';
import { useUserState } from '../../states/UserState';

/** A failed session check is retryable, not a request for new credentials. */
export function SessionRecovery() {
  const status = useUserState((state) => state.authStatus);
  const navigate = useNavigate();
  const location = useLocation();
  const redirect =
    location.pathname === '/logged-in' || location.pathname === '/login'
      ? location.state
      : { redirectUrl: location.pathname, queryParams: location.search };
  if (status === 'checking')
    return <Loader aria-label={t`Checking your session`} />;
  return (
    <Alert title={t`Could not verify your session`} color='yellow'>
      <Stack>
        <Text>{t`The server could not finish checking your account. Check your connection and retry. You do not need to enter your password again.`}</Text>
        <Group>
          <Button
            onClick={() => checkLoginState(navigate, redirect)}
          >{t`Retry session check`}</Button>
          <Button
            variant='subtle'
            onClick={() => doLogout(navigate)}
          >{t`Sign out`}</Button>
        </Group>
      </Stack>
    </Alert>
  );
}
