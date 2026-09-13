import { t } from '@lingui/core/macro';
import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { VoiceConsentDialog } from '../components/ai/voice/VoiceConsentDialog';
import { VoiceGlobalIndicator } from '../components/ai/voice/VoiceGlobalIndicator';
import { VoiceHandsFreeContent } from '../components/ai/voice/VoiceHandsFreeSurface';
import { ProtectedRoute } from '../components/nav/ProtectedRoute';
import { useVoiceLiveSession } from '../hooks/useVoiceLiveSession';
import { useLocalState } from '../states/LocalState';
import { useVoiceSurfaceState } from '../states/VoiceSessionState';

function VoicePage() {
  const host = useLocalState((s) => s.getHost());
  const voice = useVoiceLiveSession({
    host: new URL('api/ai/', `${host.replace(/\/$/, '')}/`)
      .toString()
      .replace(/\/$/, ''),
    enabled: true
  });
  useEffect(() => useVoiceSurfaceState.getState().closeFullscreen(), []);
  return (
    <Stack className='voice-hands-free' data-testid='voice-mobile-page'>
      <Title order={1}>{t`Voice`}</Title>
      <Group>
        <Button
          component={Link}
          to='/'
          mih={44}
          onClick={() => useLocalState.getState().setAllowMobile(true)}
        >
          {t`Open full app (not optimized for phones)`}
        </Button>
        <Button component={Link} to='/logout' mih={44}>{t`Log out`}</Button>
      </Group>
      {voice.capability?.enabled ? (
        <>
          <VoiceGlobalIndicator embedded />
          <VoiceHandsFreeContent embedded />
          <VoiceConsentDialog />
        </>
      ) : (
        <Text component='output'>{t`Voice is unavailable. You can continue in the full app.`}</Text>
      )}
    </Stack>
  );
}

export default function VoiceMobileAppView() {
  return (
    <ProtectedRoute>
      <VoicePage />
    </ProtectedRoute>
  );
}
